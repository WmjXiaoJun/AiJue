# 造片局 · API 契约 v1（demo）
Base URL: http://127.0.0.1:8090/api
静态资源: http://127.0.0.1:8090/media/*  ；前端构建产物由后端挂在 /
约定: JSON snake_case；时间为 ISO8601；错误返回 {"detail": "..."}；demo 无鉴权。

## 通用对象
Project: {id,name,format_id,aspect,status,budget,spent,created_at,cover,episode_count}
Episode: {id,project_id,no,title,target_duration,status,progress,shots_total,shots_done,cost,render_url,created_at}
Asset:   {id,episode_id,role,type,name,title,persona,voice,accent_color,images,locked}
         role: host|guest ; type: character|scene|prop|style
Shot:    {id,episode_id,segment_id,no,type,duration,speaker,speaker_name,line,visual,
          lower_third,punchline,sfx,mood,status,selected_version_id}
         type: title_card|host_to_camera|guest_intro|shot_reverse|reaction|b_roll|meme_card|end_card
         status: pending|queued|generating|ready|failed
ShotVersion: {id,shot_id,version,status,video_url,thumb_url,cost,params,created_at}
Job:     {id,type,status,progress,message,episode_id,shot_id,output_url,cost,error,created_at,updated_at}
         type: shot|render ; status: queued|running|done|failed|canceled
Settings:{generation_mode:"mock|comfyui",comfyui_url,workflow_template,resolution,fps,
          tts_provider,voice_map,h3_price_per_second}

## 接口
GET  /health -> {status:"ok",version,mode}
GET  /projects -> Project[]
POST /projects {name,format_id,aspect?} -> Project
GET  /projects/{pid} -> Project
GET  /projects/{pid}/episodes -> Episode[]
GET  /episodes/{eid} -> Episode
PATCH /episodes/{eid} {title?,target_duration?} -> Episode
GET  /episodes/{eid}/assets -> Asset[]
POST /episodes/{eid}/assets {...NewAsset} -> Asset
PATCH /assets/{aid} {locked?,voice?,persona?} -> Asset
GET  /episodes/{eid}/shots -> Shot[]
PATCH /shots/{sid} {line?,duration?,status?,visual?,punchline?} -> Shot
POST /shots/{sid}/generate {params?} -> Job            # mock 由 pipeline CLI 生成 5-6s 片段
GET  /shots/{sid}/versions -> ShotVersion[]
POST /shots/{sid}/versions/{vid}/select -> Shot
POST /episodes/{eid}/storyboard/generate -> {created: n}  # 从台本拆镜头（demo 直接使用种子数据）
POST /episodes/{eid}/render {} -> Job
GET  /jobs?episode_id=&type=&status= -> Job[]
GET  /jobs/{jid} -> Job
POST /jobs/{jid}/retry -> Job
GET  /episodes/{eid}/costs -> {currency:"CNY",total,by_type,items:[{label,amount}]}
GET  /settings -> Settings
PATCH /settings {...Settings} -> Settings
GET  /events?episode_id= -> SSE（可选；前端默认 2s 轮询 /jobs 与 /episodes/{eid}）

## 任务语义
- shot 任务：排队→运行→完成；生成 1 个版本，写回 Shot.selected_version_id 与 video_url；
- render 任务：串联 episode 的 shots，生成 workdir，再合成 final.mp4；progress 0-100；
- 所有任务落库，进程内线程执行即可（demo 不需要 Redis）。

## 静态与输出
- 生成片段：outputs/episodes/{eid}/shots/{sid}.mp4
- 成片：outputs/episodes/{eid}/final.mp4，同时返回 /media/episodes/{eid}/final.mp4
- 由后端 StaticFiles 挂 /media -> 仓库 outputs/
