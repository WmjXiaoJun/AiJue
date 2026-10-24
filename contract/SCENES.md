# 场景制作（2D 场景 / 3D 场景）v1

用户反馈：现有成片「全是平面的，没有 3D、也没有 2D 的场景」。目标：所有模板/成片镜头都要有真实场景空间，角色不再贴在渐变底板上；照 OpenMontage 的 scene/asset 制作方法做「2D 绘景 + 3D 场景 + 分层合成 + 镜头运动」。

## 1. 资产
### 2D 场景绘景（SDXL，imagegen）
- 输出 outputs/scene_plates/{scene_id}.png（1280×720，2D 动漫绘景，无人物），prompt/meta 记录。
- 必备场景：late_night_desk（深夜秀办公桌）、roundtable_studio（圆桌演播厅）、taskmaster_stage（任务舞台）、history_study（历史书房/烛光）、debate_hall（辩论场）、mystery_room（推理案发现场）、treasure_museum（国宝展厅）、kitchen_warm（慢生活厨房）、stage_music（舞台/蒙面唱将）、talk_show_sofa（访谈沙发）、alley_night（街头外景）、classroom_history（历史课堂）。
- 另出 3 张 3D 渲染风绘景：3d_roundtable、3d_stage、3d_study（stylized 3D render, isometric/cinematic，作为 3D 场景备选）。
### 3D 场景（Blender，studio3d）
- 输出 outputs/3d_scenes/{id}.mp4（1280×720/25fps/5-8s）：真实 3D 演播厅 set（圆桌/主持台/背景屏/灯架/地面反射）+ 3 个 3D 卡通角色入座 + 灯光 + 相机动画（缓推/横移/摇摄）。
- 至少 3 条：3d_studio_wide（横移全景）、3d_studio_host（缓推特写）、3d_stage_pan（舞台摇镜）；附静帧。

## 2. 场景映射
contract/scene_map.json：{template_id: {"plate": "history_study", "3d": optional_id, "camera": "slow push-in"}}；模板没有映射时回退现有 mock。

## 3. 合成（pipeline/scenes.py）
- 背景 plate（可做缓慢推/移的 2.5D 视差）+ 角色透明 cutout（outputs/characters/{id}/cutout/portrait.png 或 2d/）分层放置：近景角色、中景道具、远景背景；
- 每个镜头按 shot_grammar 的 movement 做相机运动（背景与角色不同速率产生视差），景别/机位沿用 ShotPlan；
- 镜头类型：host 中景、guest 中近景、reaction 特写、b_roll 空镜（纯场景）、title/end 场景+字卡；
- 输出 1280×720 25fps 镜头片段，供 render 拼接；H3 动态镜头仍可叠加（后期阶段再融合场景首帧）。

## 4. 验收
- history_talk：全部镜头用 history_study 2D 场景 + 角色分层 + 镜头运动，出 30-45s demo；
- tonight_show：用 3D 场景片段（wide/host/stage_pan）+ 2D 绘景混合，出 30-45s demo；
- 抽帧可见 3D 场景纵深/灯光/相机运动与 2D 绘景环境，不再是纯渐变底；
- QC 通过；模板验证片可批量升级（至少 6 个模板）。
