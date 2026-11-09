-- Only migrate the untouched office defaults. Preserve operator-customized dock positions.
UPDATE config x JOIN config y ON y.cfg_key='dock_y'
SET x.cfg_value='2.0', y.cfg_value='-3.0',
    x.remark='Factory charging pose X', y.remark='Factory charging pose Y'
WHERE x.cfg_key='dock_x' AND x.cfg_value='10.433' AND y.cfg_value='-5.575';
