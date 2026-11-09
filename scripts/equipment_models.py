"""Procedural, inspectable equipment assets. No downloaded/sample inspection photographs."""
import json
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"robot_ws/src/inspection_robot"
ASSETS=OUT/"assets/equipment"


def font(size):
    return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",size)


def text(draw,xy,value,size,fill,anchor="mm"):
    draw.text(xy,value,font=font(size),fill=fill,anchor=anchor)


def textures():
    ASSETS.mkdir(parents=True,exist_ok=True)
    for state,value in [("normal",.45),("abnormal",1.20)]:
        image=Image.new("RGB",(1024,1024),"#f7f8ef");d=ImageDraw.Draw(image)
        d.ellipse((60,60,964,964),fill="#e4e8e1",outline="#273a3a",width=22)
        d.ellipse((90,90,934,934),fill="#fafbf5")
        d.arc((140,140,884,884),150,180,fill="#c8a550",width=38)
        d.arc((140,140,884,884),180,270,fill="#3d966c",width=38)
        d.arc((140,140,884,884),270,390,fill="#c64f3a",width=38)
        for i in range(17):
            angle=math.radians(150+i*15)
            c,s=math.cos(angle),math.sin(angle)
            d.line((512+c*345,512+s*345,512+c*380,512+s*380),fill="#304345",width=5)
            if i%4==0:
                text(d,(512+c*300,512+s*300),f"{i/10:.1f}",40,"#304345")
        a=math.radians(150+value/1.6*240)
        d.line((512,512,512+math.cos(a)*300,512+math.sin(a)*300),fill="#c14d38",width=16)
        d.ellipse((490,490,534,534),fill="#263f3c")
        text(d,(512,285),"AIR-001",48,"#3a514c")
        text(d,(512,685),f"{value:.2f} MPa",78,"#193f36")
        text(d,(512,795),"RANGE 0.20 - 0.80 MPa",35,"#4d6558")
        image.save(ASSETS/f"gauge_{state}.png")
    for label,title in [("cabinet","ELEC-001"),("pipe","PIPE-001")]:
        image=Image.new("RGB",(1024,256),"#244d40");d=ImageDraw.Draw(image)
        text(d,(512,128),title,110,"#eff5db")
        image.save(ASSETS/f"{label}_label.png")


def material(color,texture=None):
    pbr=f"<pbr><metal><albedo_map>file://{ASSETS/texture}</albedo_map><metalness>0</metalness><roughness>.8</roughness></metal></pbr>" if texture else ""
    return f"<material><ambient>{color}</ambient><diffuse>{color}</diffuse>{pbr}</material>"


def part(name,x,y,z,sx,sy,sz,color=".7 .75 .7 1",yaw=0,collision=False,texture=None):
    geom=f"<box><size>{sx} {sy} {sz}</size></box>"
    col=f'<collision name="{name}_c"><geometry>{geom}</geometry></collision>' if collision else ""
    return f'<link name="{name}"><pose>{x} {y} {z} 0 0 {yaw}</pose>{col}<visual name="{name}_v"><geometry>{geom}</geometry>{material(color,texture)}</visual></link>'


def sphere(name,x,y,z,r,color):
    return f'<link name="{name}"><pose>{x} {y} {z} 0 0 0</pose><visual name="v"><geometry><sphere><radius>{r}</radius></sphere></geometry>{material(color)}</visual></link>'


def cylinder(name,x,y,z,r,length,color,rpy="0 0 0",collision=False):
    geometry=f"<cylinder><radius>{r}</radius><length>{length}</length></cylinder>"
    col=f'<collision name="c"><geometry>{geometry}</geometry></collision>' if collision else ""
    return f'<link name="{name}"><pose>{x} {y} {z} {rpy}</pose>{col}<visual name="v"><geometry>{geometry}</geometry>{material(color)}</visual></link>'


def model(name,x,y,z,parts):
    return f'<model name="{name}"><static>true</static><pose>{x} {y} {z} 0 0 0</pose>{"".join(parts)}</model>'


def equipment_models():
    textures()
    data=json.loads((ROOT/"shared/equipment_catalog.json").read_text())
    models=[];obstacles=[];states={}
    for asset in data["equipment"]:
        ident=asset["id"];p=asset["model"];x,y=p["x"],p["y"]
        slug=ident.lower().replace("-","_")
        base=[]
        if asset["type"]=="control_cabinet":
            base=[
                part("back",0,-.25,.9,1,.1,1.8,collision=True),
                part("left",-.48,0,.9,.08,.6,1.8,collision=True),
                part("right",.48,0,.9,.08,.6,1.8,collision=True),
                part("top",0,0,1.78,1,.6,.08,collision=True),
                part("interior",0,-.15,.85,.8,.04,1.4,".14 .20 .19 1"),
                part("label",0,.31,1.68,.78,.02,.2,"1 1 1 1",texture="cabinet_label.png"),
            ]
            normal=[part("door",0,.32,.82,.88,.055,1.45,".65 .72 .68 1"),
                    part("handle",.32,.36,.82,.035,.03,.25,".12 .18 .17 1"),
                    sphere("green_light",.3,.36,1.5,.075,".05 .95 .15 1")]
            angle=math.radians(75)
            abnormal=[part("door_open",-.44+.44*math.cos(angle),.32+.44*math.sin(angle),.82,.88,.055,1.45,".65 .72 .68 1",yaw=angle),
                      sphere("red_light",.3,.36,1.5,.075,"1 .025 .01 1")]
        elif asset["type"]=="pressure_gauge":
            base=[cylinder("tank",0,0,.48,.34,1.2,".35 .47 .48 1",rpy="0 1.570796 0",collision=True),
                  part("foot_a",-.4,0,.12,.12,.6,.24,".2 .3 .3 1",collision=True),
                  part("foot_b",.4,0,.12,.12,.6,.24,".2 .3 .3 1",collision=True),
                  part("gauge_support",0,0,.95,.08,.08,.6,".35 .45 .44 1")]
            normal=[part("gauge_face",0,.43,1.2,.9,.03,.9,"1 1 1 1",texture="gauge_normal.png")]
            abnormal=[part("gauge_face",0,.43,1.2,.9,.03,.9,"1 1 1 1",texture="gauge_abnormal.png")]
        else:
            base=[cylinder("pipe",0,0,.65,.12,1.6,".28 .47 .48 1",rpy="0 1.570796 0",collision=True),
                  cylinder("joint",0,0,.65,.2,.13,".55 .62 .6 1",rpy="0 1.570796 0"),
                  cylinder("valve_stem",0,0,.93,.045,.45,".42 .5 .47 1"),
                  cylinder("valve_wheel",0,0,1.15,.22,.05,".73 .25 .12 1"),
                  part("label",-.42,.14,.65,.65,.02,.16,"1 1 1 1",texture="pipe_label.png"),
                  part("support_a",-.6,0,.25,.12,.45,.5,".3 .4 .39 1",collision=True),
                  part("support_b",.6,0,.25,.12,.45,.5,".3 .4 .39 1",collision=True)]
            normal=[part("dry_marker",0,0,.005,.01,.01,.005,".8 .82 .77 1")]
            abnormal=[cylinder("puddle_a",-.3,.55,.012,.45,.02,".08 .4 .65 1"),
                      cylinder("puddle_b",.28,.67,.012,.48,.02,".08 .4 .65 1"),
                      cylinder("puddle_c",.06,.35,.012,.38,.02,".08 .4 .65 1"),
                      sphere("drop_a",0,.2,.39,.045,".04 .45 .95 1"),
                      sphere("drop_b",.02,.2,.23,.04,".04 .45 .95 1"),
                      sphere("drop_c",-.02,.2,.08,.035,".04 .45 .95 1")]
        models.append(model(slug+"_body",x,y,0,base))
        models.append(model(slug+"_normal",x,y,0,normal))
        models.append(model(slug+"_abnormal",x,y,-20,abnormal))
        states[ident]={"x":x,"y":y,"normal_model":slug+"_normal","abnormal_model":slug+"_abnormal"}
        obstacles.append((x,y,p["width"],p["depth"]))
    (OUT/"config").mkdir(parents=True,exist_ok=True)
    (OUT/"config/equipment_catalog.json").write_text(json.dumps(data,ensure_ascii=False,indent=2))
    (OUT/"config/equipment_states.json").write_text(json.dumps(states,indent=2))
    return models,obstacles
