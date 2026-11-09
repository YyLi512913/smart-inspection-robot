"""Deterministic RMF executor with durable command IDs and stop confirmation.

No command may replace an active execution. Restarted work is held for recovery,
never silently restarted. Switching the navigation adapter to Nav2 is a later step.
"""
import json
import math
import time
from uuid import NAMESPACE_URL, uuid5

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from .durable_store import DurableStore, default_path

try:
    from rmf_fleet_msgs.msg import FleetState
    from rmf_task_msgs.msg import Loop, TaskDescription, TaskType
    from rmf_task_msgs.srv import CancelTask, SubmitTask
    HAS_RMF = True
except ImportError:
    HAS_RMF = False

PLACES = {
    'tinyRobot1_charger': (2.0, -3.0), 'corridor_west': (5.75, -3.0),
    'corridor_mid': (14.75, -3.0), 'corridor_east': (21.75, -3.0),
    'workshop': (5.75, -11.0), 'power_room': (14.75, -11.0),
    'server_room': (21.75, -11.0),
}


def nearest_places(x, y, n=1):
    return sorted(PLACES, key=lambda k: (PLACES[k][0]-x)**2+(PLACES[k][1]-y)**2)[:n]


class TaskRunner(Node):
    def __init__(self):
        super().__init__('task_runner')
        for key, value in [('robot_id','robot1'), ('fleet_robot_name','tinyRobot1'),
                           ('robot_type','tinyRobot'), ('arrive_threshold_m',.5),
                           ('nav_timeout_s',240.0), ('journal_path','')]:
            self.declare_parameter(key,value)
        self.robot_name = self.get_parameter('fleet_robot_name').value
        self.robot_type = self.get_parameter('robot_type').value
        self.arrive_m = float(self.get_parameter('arrive_threshold_m').value)
        self.nav_timeout = float(self.get_parameter('nav_timeout_s').value)
        path = self.get_parameter('journal_path').value or default_path(self.get_parameter('robot_id').value)
        self.store = DurableStore(path)
        try:
            self.execution_lock = self.store.acquire_execution_lock()
        except Exception:
            Node.destroy_node(self)
            raise
        if not HAS_RMF:
            raise RuntimeError('RMF messages unavailable; executor cannot start')
        self.robot_loc = None
        self.robot_loc_at = 0
        self.task = None
        self.stop_request = None
        self.stop_future = None
        self.stop_sent_at = 0
        self.event_pub = self.create_publisher(String,'/inspection/event',10)
        self.snapshot_pub = self.create_publisher(String,'/inspection/snapshot_request',10)
        self.current_pub = self.create_publisher(String,'/inspection/current_task',10)
        self.create_subscription(String,'/inspection/task',self._task_cb,10)
        self.create_subscription(FleetState,'/fleet_states',self._fleet_cb,10)
        self.submit_cli = self.create_client(SubmitTask,'/submit_task')
        self.cancel_cli = self.create_client(CancelTask,'/cancel_task')
        active = self.store.active()
        if active:
            self.task = json.loads(active['runtime']) if active['runtime'] else {
                'task_id':active['task_id'],'command_id':active['command_id'],
                'points':json.loads(active['payload'])['points'], 'idx':0,
                'phase':'idle','dispatch_id':None}
            self.task['recovery_phase'] = self.task['phase']
            self.task['phase'] = 'recovery'
            self._emit('recovery_required',detail={'reason':'executor_restart','previous_phase':self.task['recovery_phase']})
        self.create_timer(.5,self._tick)
        self._publish_current()

    def destroy_node(self):
        lock = getattr(self, "execution_lock", None)
        result = Node.destroy_node(self)
        if lock:
            lock.close()
            self.execution_lock = None
        return result

    def _save(self):
        if self.task:
            self.store.save_runtime(self.task['command_id'],self.task)

    def _task_cb(self,msg):
        try:
            payload=json.loads(msg.data)
            if not isinstance(payload,dict) or payload.get('protocol_version')!=2:
                raise ValueError('protocol v2 required')
            if not all(isinstance(payload.get(k),str) and 0<len(payload[k])<=64 for k in ('command_id','task_id')):
                raise ValueError('command_id/task_id required')
            if payload.get('action')=='cancel':
                self._cancel_task(payload)
            elif payload.get('action')=='patrol':
                self._start_task(payload)
        except (ValueError,TypeError,KeyError) as exc:
            self.get_logger().error(f'拒绝无效任务: {exc}')

    def _start_task(self,payload):
        points=payload.get('points')
        if not isinstance(points,list) or not points:
            raise ValueError('points required')
        catalog=getattr(self,"point_catalog",PLACES)
        seen=set()
        for p in points:
            if not isinstance(p,dict) or type(p.get('seq')) is not int or p['seq']<=0 or p['seq'] in seen:
                raise ValueError('unique positive point sequence required')
            if p.get('place') not in catalog or not all(math.isfinite(float(p[k])) for k in ('x','y')):
                raise ValueError('invalid point location')
            if math.hypot(float(p['x'])-catalog[p['place']][0],float(p['y'])-catalog[p['place']][1])>.05:
                raise ValueError('place and coordinates disagree')
            seen.add(p['seq'])
        result=self.store.begin(payload)
        if result=='duplicate':
            stored = self.store.command(payload['command_id'])
            if stored['state'] == 'rejected' and not stored['last_event']:
                self.store.emit({'task_id':payload['task_id'],'command_id':payload['command_id'],
                    'event':'command_rejected','detail':{'reason':'previously_rejected'}}, state='rejected')
            else:
                self.store.replay(payload['command_id'])
            return
        if result!='new':
            rejected={'task_id':payload['task_id'],'command_id':payload['command_id'],
                'event':'command_rejected','detail':{'reason':result}}
            if result == 'conflict':
                self.store.enqueue('event',rejected)
            else:
                self.store.emit(rejected,state='rejected')
            return
        self.task={'task_id':payload['task_id'],'command_id':payload['command_id'],
                   'points':points,'idx':0,'dispatch_id':None,'phase':'idle','sent_at':None}
        self._save()
        self._emit('accepted')
        self._publish_current()
        self._send_current_point()

    def _cancel_task(self,payload):
        if self.stop_request:
            # Same cancel is retried by the backend; don't reset the stop future.
            return
        self.stop_request={'kind':'cancel','payload':payload}
        self._advance_stop()

    def _advance_stop(self):
        stop=self.stop_request
        if not stop:
            return
        matches=self.task and (stop['kind']=='blocked' or self.task['task_id']==stop['payload']['task_id'])
        if not matches:
            self._finish_stop()
            return
        # A pending submit may already have executed remotely. Wait for its result.
        phase=self.task.get('recovery_phase',self.task['phase'])
        if self.task['phase']=='submitting' or (self.task['phase']=='recovery' and phase=='submitting'):
            return
        dispatch_id=self.task.get('dispatch_id')
        if not dispatch_id:
            self._finish_stop()
            return
        if self.stop_future and not self.stop_future.done() and time.monotonic()-self.stop_sent_at<5:
            return
        if not self.cancel_cli.service_is_ready():
            return
        req=CancelTask.Request()
        req.requester='inspection_task_runner'
        req.task_id=dispatch_id
        future=self.cancel_cli.call_async(req)
        self.stop_future=future
        self.stop_sent_at=time.monotonic()
        def confirmed(f):
            if self.stop_request is not stop:
                return
            try:
                response=f.result()
                if not response.success:
                    self.get_logger().warn('RMF 尚未确认取消，保留机器人占用')
                    return
            except Exception as exc:
                self.get_logger().error(f'取消失败，将重试: {exc}')
                return
            self._finish_stop()
        future.add_done_callback(confirmed)

    def _finish_stop(self):
        stop=self.stop_request
        if stop['kind']=='cancel':
            payload=stop['payload']
            self.store.cancel(payload)
            self.store.enqueue('event',{'event_id':uuid5(NAMESPACE_URL,payload['command_id']+':cancel_confirmed').hex,
                'task_id':payload['task_id'],'command_id':payload['command_id'],
                'event':'cancel_confirmed','detail':{'adapter':getattr(self,'adapter_name','rmf')}})
            if self.task and self.task['task_id']==payload['task_id']:
                self.task=None
        else:
            self._emit('blocked',stop['seq'],stop['detail'],state='blocked')
            self.task=None
        self.stop_request=None
        self.stop_future=None
        self._publish_current()

    def _fleet_cb(self,msg):
        for rs in msg.robots:
            if rs.name==self.robot_name:
                self.robot_loc=(rs.location.x,rs.location.y)
                self.robot_loc_at=time.monotonic()
                return

    def _send_current_point(self):
        if not self.task or self.stop_request:
            return
        p=self.task['points'][self.task['idx']]
        dest=p['place']
        p['dest_xy']=PLACES[dest]
        self._emit('navigating',p['seq'])
        self._publish_current()
        if self.robot_loc and time.monotonic()-self.robot_loc_at<5 and math.dist(self.robot_loc,PLACES[dest])<self.arrive_m:
            self._on_arrived(p)
            return
        if not self.robot_loc or time.monotonic()-self.robot_loc_at>5:
            self._on_stuck(p,'定位数据不可用')
            return
        if not self.submit_cli.service_is_ready():
            self._on_stuck(p,'submit_task 服务不可用')
            return
        ranked=nearest_places(*self.robot_loc,2)
        start=ranked[0] if ranked[0]!=dest else ranked[1]
        req=SubmitTask.Request()
        req.requester='inspection_task_runner'
        req.description=TaskDescription()
        req.description.task_type=TaskType(type=TaskType.TYPE_LOOP)
        req.description.loop=Loop(task_id=f"insp-{self.task['command_id']}-{p['seq']}",
            robot_type=self.robot_type,num_loops=1,start_name=start,finish_name=dest)
        self.task['phase']='submitting'
        self.task['sent_at']=time.monotonic()
        self._save()  # crash during submit leaves an explicit unresolved state
        command_id=self.task['command_id']
        self.submit_cli.call_async(req).add_done_callback(lambda future:self._submit_cb(future,command_id))

    def _submit_cb(self,future,command_id):
        if not self.task or self.task['command_id']!=command_id:
            return
        try:
            response=future.result()
            if not response.success:
                self.task['phase']='idle'
                self._save()
                if self.stop_request:self._advance_stop()
                else:self._on_stuck(self.task['points'][self.task['idx']],f'submit 被拒: {response.message}')
                return
        except Exception as exc:
            # Transport failure doesn't prove the dispatcher rejected the task.
            self.task['phase']='recovery'
            self.task['recovery_phase']='submitting'
            self._save()
            self._emit('recovery_required',detail={'reason':str(exc)})
            return
        self.task['dispatch_id']=response.task_id
        self.task['phase']='navigating'
        self.task['sent_at']=time.monotonic()
        self._save()
        if self.stop_request:
            self._advance_stop()

    def _tick(self):
        self._publish_current()
        if self.stop_request:
            self._advance_stop()
            return
        if not self.task or self.task['phase']!='navigating':
            return
        point=self.task['points'][self.task['idx']]
        if time.monotonic()-self.task['sent_at']>self.nav_timeout:
            self._on_stuck(point,'导航超时')
        elif self.robot_loc and time.monotonic()-self.robot_loc_at<5 and math.dist(self.robot_loc,point['dest_xy'])<self.arrive_m:
            self._on_arrived(point)

    def _on_arrived(self,point):
        request={'task_id':self.task['task_id'],'command_id':self.task['command_id'],
                 'point_seq':point['seq'],'place':point['place'],'check':point.get('check','')}
        self._emit('point_arrived',point['seq'],snapshot=request)
        self.snapshot_pub.publish(String(data=json.dumps(request,ensure_ascii=False)))
        self.task['dispatch_id']=None
        self.task['phase']='idle'
        self.task['idx']+=1
        self._save()
        if self.task['idx']>=len(self.task['points']):
            self._emit('task_done',state='done')
            self.task=None
            self._publish_current()
        else:
            self._send_current_point()

    def _on_stuck(self,point,reason):
        self.stop_request={'kind':'blocked','seq':point['seq'],'detail':{'reason':reason}}
        self._advance_stop()

    def _emit(self,event,point_seq=None,detail=None,state=None,snapshot=None):
        payload={'task_id':self.task['task_id'],'command_id':self.task['command_id'],
                 'event':event,'point_seq':point_seq,'detail':detail,
                 'ts':int(time.time()*1000)}
        payload=self.store.emit(payload,state=state,snapshot=snapshot)
        self.event_pub.publish(String(data=json.dumps(payload,ensure_ascii=False)))

    def _publish_current(self):
        current=self.task['task_id'] if self.task else None
        self.current_pub.publish(String(data=json.dumps(current)))


def main(args=None):
    rclpy.init(args=args)
    node=TaskRunner()
    try:rclpy.spin(node)
    except KeyboardInterrupt:pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
