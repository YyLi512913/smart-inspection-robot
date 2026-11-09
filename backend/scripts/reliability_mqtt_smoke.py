"""Real broker + ROS-node smoke test. Uses an isolated MySQL database."""
import json
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from test_reliability import ReliabilityTests
from app import config, db, delivery, state_machine as sm
from app.mqtt_consumer import MqttConsumer

def wait(predicate, seconds=20):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        delivery.flush_pending()
        if predicate():return
        time.sleep(.2)
    raise AssertionError('Condition did not become true within timeout')

ReliabilityTests.setUpClass()
consumer=None
try:
    config.MQTT_PORT=18884
    config.MQTT_CLIENT_ID="reliability-acceptance-"+__import__("uuid").uuid4().hex
    consumer=MqttConsumer()
    consumer.start()
    delivery.set_publisher(consumer.publish)
    wait(consumer.c.is_connected)
    points=[{'place':'power_room','x':14.75,'y':-11.0,'check_item':'test'}]
    no=sm.create_task('reliabilitytest','manual',5,'smoke',points,{'verdict':'feasible'})
    status=lambda:db.query_one('SELECT status FROM task WHERE task_no=:n',{'n':no})['status']
    wait(lambda:status()=='BLOCKED')
    cmd=db.query_one("SELECT * FROM mqtt_command WHERE task_no=:n AND action='patrol'",{'n':no})
    consumer.publish('robot/reliabilitytest/task',json.loads(cmd['payload']))
    time.sleep(2)
    counts=db.query("SELECT event,COUNT(*) AS c FROM task_event WHERE task_id=(SELECT id FROM task WHERE task_no=:n) GROUP BY event",{'n':no})
    events={r['event']:r['c'] for r in counts}
    assert events['accepted']==1 and events['blocked']==1,events
    assert sm.decision(no,'skip')=='DONE'
    consumer.stop()
    second=sm.create_task('reliabilitytest','manual',5,'smoke',points,{'verdict':'feasible'})
    delivery.flush_pending()
    assert db.query_one("SELECT status FROM mqtt_command WHERE task_no=:n", {'n':second})['status']=='pending'
    consumer.start()
    wait(consumer.c.is_connected)
    sm.cancel(second) # cancel before delivery; no patrol may execute
    wait(lambda:db.query_one('SELECT task_no FROM robot_execution WHERE robot_id=:r',{'r':'reliabilitytest'})['task_no'] is None)
    assert not db.query("SELECT id FROM task_event WHERE task_id=(SELECT id FROM task WHERE task_no=:n) AND event='accepted'",{'n':second})
    print(json.dumps({'real_mqtt':True,'real_ros_nodes':True,'accepted_once':True,'blocked_once':True,
                      'skip_completed':True,'cancel_confirmed':True,'database_isolated':True,'backend_reconnect':True}))
finally:
    if consumer:consumer.stop()
    delivery.set_publisher(None)
    ReliabilityTests.tearDownClass()
