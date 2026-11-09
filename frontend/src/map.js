// 米坐标 ↔ building.yaml 像素坐标换算：factory 地图 100px=1m（measurement 强制 scale=0.01），
// y 轴翻转，原点取墙体最大 y（factory 地图 y_m∈[-16,0]，H=1600px）
export const MAP_H_PX = 1600.0

export function toPx(xM, yM) {
  return [xM * 100, MAP_H_PX + yM * 100]
}

export async function loadMap() {
  const r = await fetch('/factory_map.json')
  return r.json()
}
