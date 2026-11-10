// 节目模式静态说明（展示用；与 contract/formats/*.json 对齐）
export interface TemplateSegmentInfo {
  id: string;
  name: string;
  duration: number;
  pattern: string[];
}

export interface TemplateInfo {
  id: string;
  name: string;
  tagline: string;
  aspect: string;
  target_duration: number;
  accent: string;
  segments: TemplateSegmentInfo[];
  packaging: [string, string][];
}

export const TEMPLATES: TemplateInfo[] = [
  {
    "id": "roundtable_5min",
    "name": "横屏 AI 圆桌 · 5 分钟",
    "tagline": "横屏 AI 圆桌：主持 + 2 嘉宾，正反打 + 反应镜头 + 花字收口",
    "aspect": "16:9",
    "target_duration": 296,
    "accent": "#FB923C",
    "segments": [
      {
        "id": "open",
        "name": "片头",
        "duration": 6,
        "pattern": [
          "title_card"
        ]
      },
      {
        "id": "cold_open",
        "name": "冷开场",
        "duration": 17,
        "pattern": [
          "host_to_camera",
          "reaction"
        ]
      },
      {
        "id": "intro",
        "name": "嘉宾介绍",
        "duration": 32,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction"
        ]
      },
      {
        "id": "topic1",
        "name": "话题一 · AI 与导演",
        "duration": 113,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction",
          "meme_card",
          "b_roll"
        ]
      },
      {
        "id": "topic2",
        "name": "话题二 · 现场改台本",
        "duration": 76,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction",
          "meme_card",
          "b_roll"
        ]
      },
      {
        "id": "closing",
        "name": "结尾",
        "duration": 52,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "meme_card",
          "end_card"
        ]
      }
    ],
    "packaging": [
      [
        "片头",
        "logo_sting"
      ],
      [
        "片尾",
        "end_card"
      ],
      [
        "花字",
        "zaopianju_yellow"
      ],
      [
        "转场",
        "hard_cut"
      ],
      [
        "BGM",
        "talk_light"
      ],
      [
        "字幕",
        "variety"
      ]
    ]
  },
  {
    "id": "tonight_show_5min",
    "name": "美式深夜秀 · 5 分钟",
    "tagline": "美式深夜秀：独白开场 + 坐访对谈 + 乐队彩蛋",
    "aspect": "16:9",
    "target_duration": 312,
    "accent": "#FB7185",
    "segments": [
      {
        "id": "open",
        "name": "片头",
        "duration": 6,
        "pattern": [
          "title_card"
        ]
      },
      {
        "id": "monologue",
        "name": "开场独白",
        "duration": 50,
        "pattern": [
          "host_to_camera",
          "reaction",
          "meme_card",
          "b_roll"
        ]
      },
      {
        "id": "guest_entrance",
        "name": "嘉宾登场",
        "duration": 38,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction"
        ]
      },
      {
        "id": "interview",
        "name": "明星访谈",
        "duration": 90,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction",
          "b_roll",
          "meme_card"
        ]
      },
      {
        "id": "game",
        "name": "互动游戏 / 音乐表演",
        "duration": 80,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction",
          "b_roll"
        ]
      },
      {
        "id": "closing",
        "name": "道别与预告",
        "duration": 48,
        "pattern": [
          "host_to_camera",
          "reaction",
          "end_card"
        ]
      }
    ],
    "packaging": [
      [
        "片头",
        "logo_sting"
      ],
      [
        "片尾",
        "end_card"
      ],
      [
        "花字",
        "zaopianju_yellow"
      ],
      [
        "转场",
        "hard_cut"
      ],
      [
        "BGM",
        "talk_light"
      ],
      [
        "字幕",
        "variety"
      ]
    ]
  },
  {
    "id": "snl_sketch_5min",
    "name": "SNL 式小品 · 5 分钟",
    "tagline": "SNL 式小品：冷开场 + 情景短剧 + 周末更新",
    "aspect": "16:9",
    "target_duration": 301,
    "accent": "#E879F9",
    "segments": [
      {
        "id": "open",
        "name": "片头",
        "duration": 6,
        "pattern": [
          "title_card"
        ]
      },
      {
        "id": "monologue",
        "name": "开场主持词",
        "duration": 30,
        "pattern": [
          "host_to_camera",
          "reaction"
        ]
      },
      {
        "id": "sketch1",
        "name": "小品一 · 办公室荒诞",
        "duration": 110,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction",
          "meme_card",
          "b_roll"
        ]
      },
      {
        "id": "sketch2",
        "name": "小品二 · 新闻播报恶搞",
        "duration": 100,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction",
          "meme_card",
          "b_roll"
        ]
      },
      {
        "id": "closing",
        "name": "谢幕与下周预告",
        "duration": 55,
        "pattern": [
          "host_to_camera",
          "reaction",
          "end_card"
        ]
      }
    ],
    "packaging": [
      [
        "片头",
        "logo_sting"
      ],
      [
        "片尾",
        "end_card"
      ],
      [
        "花字",
        "zaopianju_yellow"
      ],
      [
        "转场",
        "hard_cut"
      ],
      [
        "BGM",
        "talk_light"
      ],
      [
        "字幕",
        "variety"
      ]
    ]
  },
  {
    "id": "taskmaster_5min",
    "name": "任务大师 · 5 分钟",
    "tagline": "任务大师：出题 + 离谱挑战 + 主持人毒舌评分",
    "aspect": "16:9",
    "target_duration": 311,
    "accent": "#2DD4BF",
    "segments": [
      {
        "id": "open",
        "name": "片头",
        "duration": 6,
        "pattern": [
          "title_card"
        ]
      },
      {
        "id": "rules",
        "name": "主持人念任务",
        "duration": 40,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction",
          "meme_card"
        ]
      },
      {
        "id": "task1",
        "name": "任务一 · 居家挑战",
        "duration": 90,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction",
          "b_roll"
        ]
      },
      {
        "id": "task2",
        "name": "任务二 · 限时整活",
        "duration": 85,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction",
          "b_roll",
          "meme_card"
        ]
      },
      {
        "id": "scoring",
        "name": "评分与吐槽",
        "duration": 50,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction",
          "meme_card"
        ]
      },
      {
        "id": "closing",
        "name": "结尾",
        "duration": 40,
        "pattern": [
          "host_to_camera",
          "end_card"
        ]
      }
    ],
    "packaging": [
      [
        "片头",
        "logo_sting"
      ],
      [
        "片尾",
        "end_card"
      ],
      [
        "花字",
        "zaopianju_yellow"
      ],
      [
        "转场",
        "hard_cut"
      ],
      [
        "BGM",
        "talk_light"
      ],
      [
        "字幕",
        "variety"
      ]
    ]
  },
  {
    "id": "traitors_5min",
    "name": "叛徒 · 圆桌 · 5 分钟",
    "tagline": "叛徒圆桌：圆桌投票 + 背叛 + 悬念反转",
    "aspect": "16:9",
    "target_duration": 312,
    "accent": "#F5C518",
    "segments": [
      {
        "id": "open",
        "name": "片头",
        "duration": 6,
        "pattern": [
          "title_card"
        ]
      },
      {
        "id": "host_intro",
        "name": "主持人开场 · 规则与城堡",
        "duration": 48,
        "pattern": [
          "host_to_camera",
          "b_roll",
          "meme_card"
        ]
      },
      {
        "id": "roundtable",
        "name": "圆桌互相试探",
        "duration": 100,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction",
          "meme_card"
        ]
      },
      {
        "id": "banishment",
        "name": "投票放逐",
        "duration": 88,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction",
          "meme_card"
        ]
      },
      {
        "id": "closing",
        "name": "身份揭晓",
        "duration": 70,
        "pattern": [
          "host_to_camera",
          "reaction",
          "b_roll",
          "end_card"
        ]
      }
    ],
    "packaging": [
      [
        "片头",
        "logo_sting"
      ],
      [
        "片尾",
        "end_card"
      ],
      [
        "花字",
        "zaopianju_yellow"
      ],
      [
        "转场",
        "hard_cut"
      ],
      [
        "BGM",
        "talk_light"
      ],
      [
        "字幕",
        "variety"
      ]
    ]
  },
  {
    "id": "shark_tank_5min",
    "name": "创智赢家 · 5 分钟",
    "tagline": "创智赢家：创业 pitch + 投资人连环追问",
    "aspect": "16:9",
    "target_duration": 306,
    "accent": "#4ADE80",
    "segments": [
      {
        "id": "open",
        "name": "片头",
        "duration": 6,
        "pattern": [
          "title_card"
        ]
      },
      {
        "id": "pitch",
        "name": "电梯路演",
        "duration": 50,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "b_roll",
          "reaction"
        ]
      },
      {
        "id": "grill",
        "name": "投资人质询",
        "duration": 85,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction",
          "meme_card"
        ]
      },
      {
        "id": "negotiation",
        "name": "出价谈判",
        "duration": 95,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction",
          "meme_card"
        ]
      },
      {
        "id": "closing",
        "name": "成交 / 离场",
        "duration": 70,
        "pattern": [
          "host_to_camera",
          "reaction",
          "end_card"
        ]
      }
    ],
    "packaging": [
      [
        "片头",
        "logo_sting"
      ],
      [
        "片尾",
        "end_card"
      ],
      [
        "花字",
        "zaopianju_yellow"
      ],
      [
        "转场",
        "hard_cut"
      ],
      [
        "BGM",
        "talk_light"
      ],
      [
        "字幕",
        "variety"
      ]
    ]
  },
  {
    "id": "masked_singer_5min",
    "name": "蒙面唱将 · 5 分钟",
    "tagline": "蒙面唱将：线索竞猜 + 舞台 + 揭面反转",
    "aspect": "16:9",
    "target_duration": 306,
    "accent": "#A78BFA",
    "segments": [
      {
        "id": "open",
        "name": "片头",
        "duration": 6,
        "pattern": [
          "title_card"
        ]
      },
      {
        "id": "rules",
        "name": "主持人介绍规则",
        "duration": 42,
        "pattern": [
          "host_to_camera",
          "meme_card",
          "reaction"
        ]
      },
      {
        "id": "performance",
        "name": "面具舞台表演",
        "duration": 100,
        "pattern": [
          "host_to_camera",
          "b_roll",
          "reaction",
          "meme_card"
        ]
      },
      {
        "id": "guessing",
        "name": "猜评团推理",
        "duration": 88,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction",
          "meme_card"
        ]
      },
      {
        "id": "closing",
        "name": "揭面时刻",
        "duration": 70,
        "pattern": [
          "host_to_camera",
          "reaction",
          "end_card"
        ]
      }
    ],
    "packaging": [
      [
        "片头",
        "logo_sting"
      ],
      [
        "片尾",
        "end_card"
      ],
      [
        "花字",
        "zaopianju_yellow"
      ],
      [
        "转场",
        "hard_cut"
      ],
      [
        "BGM",
        "talk_light"
      ],
      [
        "字幕",
        "variety"
      ]
    ]
  },
  {
    "id": "hot_ones_5min",
    "name": "辣访谈 · 5 分钟",
    "tagline": "辣访谈：边吃辣边聊 + 极限反应 + 走心收尾",
    "aspect": "16:9",
    "target_duration": 306,
    "accent": "#34D399",
    "segments": [
      {
        "id": "open",
        "name": "片头",
        "duration": 6,
        "pattern": [
          "title_card"
        ]
      },
      {
        "id": "setup",
        "name": "辣度规则介绍",
        "duration": 42,
        "pattern": [
          "host_to_camera",
          "b_roll",
          "meme_card"
        ]
      },
      {
        "id": "interview",
        "name": "边吃边聊",
        "duration": 95,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction",
          "meme_card"
        ]
      },
      {
        "id": "spicy",
        "name": "越来越辣的问题",
        "duration": 95,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction",
          "meme_card",
          "b_roll"
        ]
      },
      {
        "id": "closing",
        "name": "收尾 · 最后一口",
        "duration": 68,
        "pattern": [
          "host_to_camera",
          "reaction",
          "end_card"
        ]
      }
    ],
    "packaging": [
      [
        "片头",
        "logo_sting"
      ],
      [
        "片尾",
        "end_card"
      ],
      [
        "花字",
        "zaopianju_yellow"
      ],
      [
        "转场",
        "hard_cut"
      ],
      [
        "BGM",
        "talk_light"
      ],
      [
        "字幕",
        "variety"
      ]
    ]
  },
  {
    "id": "qipashuo_5min",
    "name": "奇葩辩论 · 5 分钟",
    "tagline": "奇葩辩论：正反方立论 + 自由辩论 + 金句收束",
    "aspect": "16:9",
    "target_duration": 306,
    "accent": "#F472B6",
    "segments": [
      {
        "id": "open",
        "name": "片头",
        "duration": 6,
        "pattern": [
          "title_card"
        ]
      },
      {
        "id": "topic",
        "name": "主持人开题",
        "duration": 45,
        "pattern": [
          "host_to_camera",
          "meme_card"
        ]
      },
      {
        "id": "opening",
        "name": "正反方开杠",
        "duration": 95,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction",
          "meme_card"
        ]
      },
      {
        "id": "free_debate",
        "name": "自由辩论",
        "duration": 95,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction",
          "meme_card",
          "b_roll"
        ]
      },
      {
        "id": "closing",
        "name": "结辩与结尾",
        "duration": 65,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction",
          "end_card"
        ]
      }
    ],
    "packaging": [
      [
        "片头",
        "logo_sting"
      ],
      [
        "片尾",
        "end_card"
      ],
      [
        "花字",
        "zaopianju_yellow"
      ],
      [
        "转场",
        "hard_cut"
      ],
      [
        "BGM",
        "talk_light"
      ],
      [
        "字幕",
        "variety"
      ]
    ]
  },
  {
    "id": "standup_5min",
    "name": "脱口秀大会 · 5 分钟",
    "tagline": "脱口秀大会：开放麦 + 爆梗 + 领笑员点评",
    "aspect": "16:9",
    "target_duration": 306,
    "accent": "#FACC15",
    "segments": [
      {
        "id": "open",
        "name": "片头",
        "duration": 6,
        "pattern": [
          "title_card"
        ]
      },
      {
        "id": "warmup",
        "name": "主持人热场",
        "duration": 40,
        "pattern": [
          "host_to_camera",
          "reaction",
          "meme_card"
        ]
      },
      {
        "id": "set1",
        "name": "选手段子一",
        "duration": 95,
        "pattern": [
          "host_to_camera",
          "b_roll",
          "reaction",
          "meme_card"
        ]
      },
      {
        "id": "set2",
        "name": "段子二 · 互动",
        "duration": 100,
        "pattern": [
          "host_to_camera",
          "b_roll",
          "reaction",
          "meme_card"
        ]
      },
      {
        "id": "closing",
        "name": "投票与结尾",
        "duration": 65,
        "pattern": [
          "host_to_camera",
          "reaction",
          "end_card"
        ]
      }
    ],
    "packaging": [
      [
        "片头",
        "logo_sting"
      ],
      [
        "片尾",
        "end_card"
      ],
      [
        "花字",
        "zaopianju_yellow"
      ],
      [
        "转场",
        "hard_cut"
      ],
      [
        "BGM",
        "talk_light"
      ],
      [
        "字幕",
        "variety"
      ]
    ]
  },
  {
    "id": "slow_life_5min",
    "name": "慢生活 · 5 分钟",
    "tagline": "慢生活：做饭 + 闲聊 + 生活哲思",
    "aspect": "16:9",
    "target_duration": 306,
    "accent": "#60A5FA",
    "segments": [
      {
        "id": "open",
        "name": "片头",
        "duration": 6,
        "pattern": [
          "title_card"
        ]
      },
      {
        "id": "scene",
        "name": "空镜与旁白",
        "duration": 50,
        "pattern": [
          "b_roll",
          "host_to_camera"
        ]
      },
      {
        "id": "cooking",
        "name": "做饭日常",
        "duration": 90,
        "pattern": [
          "host_to_camera",
          "b_roll",
          "reaction",
          "meme_card"
        ]
      },
      {
        "id": "evening",
        "name": "围炉夜话",
        "duration": 95,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction",
          "b_roll"
        ]
      },
      {
        "id": "closing",
        "name": "结尾",
        "duration": 65,
        "pattern": [
          "host_to_camera",
          "reaction",
          "end_card"
        ]
      }
    ],
    "packaging": [
      [
        "片头",
        "logo_sting"
      ],
      [
        "片尾",
        "end_card"
      ],
      [
        "花字",
        "zaopianju_yellow"
      ],
      [
        "转场",
        "hard_cut"
      ],
      [
        "BGM",
        "talk_light"
      ],
      [
        "字幕",
        "variety"
      ]
    ]
  },
  {
    "id": "mystery_5min",
    "name": "推理现场 · 5 分钟",
    "tagline": "推理现场：案发现场 + 两轮线索 + 投票揭晓",
    "aspect": "16:9",
    "target_duration": 316,
    "accent": "#F97316",
    "segments": [
      {
        "id": "open",
        "name": "片头",
        "duration": 6,
        "pattern": [
          "title_card"
        ]
      },
      {
        "id": "case",
        "name": "案件背景 · 情景再现",
        "duration": 50,
        "pattern": [
          "host_to_camera",
          "b_roll",
          "meme_card"
        ]
      },
      {
        "id": "clues",
        "name": "搜证与线索",
        "duration": 85,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "b_roll",
          "meme_card"
        ]
      },
      {
        "id": "reasoning",
        "name": "集中推理",
        "duration": 95,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction",
          "meme_card"
        ]
      },
      {
        "id": "closing",
        "name": "投票与揭晓",
        "duration": 80,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction",
          "end_card"
        ]
      }
    ],
    "packaging": [
      [
        "片头",
        "logo_sting"
      ],
      [
        "片尾",
        "end_card"
      ],
      [
        "花字",
        "zaopianju_yellow"
      ],
      [
        "转场",
        "hard_cut"
      ],
      [
        "BGM",
        "talk_light"
      ],
      [
        "字幕",
        "variety"
      ]
    ]
  },
  {
    "id": "national_treasure_5min",
    "name": "国宝守护 · 5 分钟",
    "tagline": "国宝守护：文物前世传奇 + 今生讲述",
    "aspect": "16:9",
    "target_duration": 306,
    "accent": "#22D3EE",
    "segments": [
      {
        "id": "open",
        "name": "片头",
        "duration": 6,
        "pattern": [
          "title_card"
        ]
      },
      {
        "id": "opening",
        "name": "讲解员开场",
        "duration": 45,
        "pattern": [
          "host_to_camera",
          "b_roll",
          "meme_card"
        ]
      },
      {
        "id": "legend",
        "name": "前世传奇 · 情景剧",
        "duration": 100,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction",
          "b_roll"
        ]
      },
      {
        "id": "story",
        "name": "今生故事 · 守护人",
        "duration": 85,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction",
          "b_roll"
        ]
      },
      {
        "id": "closing",
        "name": "结尾",
        "duration": 70,
        "pattern": [
          "host_to_camera",
          "reaction",
          "end_card"
        ]
      }
    ],
    "packaging": [
      [
        "片头",
        "logo_sting"
      ],
      [
        "片尾",
        "end_card"
      ],
      [
        "花字",
        "zaopianju_yellow"
      ],
      [
        "转场",
        "hard_cut"
      ],
      [
        "BGM",
        "talk_light"
      ],
      [
        "字幕",
        "variety"
      ]
    ]
  },
  {
    "id": "classics_5min",
    "name": "典籍对话 · 5 分钟",
    "tagline": "典籍对话：古今对话 + 典籍故事 + 当代回响",
    "aspect": "16:9",
    "target_duration": 311,
    "accent": "#F5C518",
    "segments": [
      {
        "id": "open",
        "name": "片头",
        "duration": 6,
        "pattern": [
          "title_card"
        ]
      },
      {
        "id": "opening",
        "name": "主持人开场",
        "duration": 45,
        "pattern": [
          "host_to_camera",
          "meme_card",
          "b_roll"
        ]
      },
      {
        "id": "drama",
        "name": "典籍情景演绎",
        "duration": 100,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction",
          "b_roll"
        ]
      },
      {
        "id": "dialogue",
        "name": "古今对话",
        "duration": 90,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction",
          "meme_card"
        ]
      },
      {
        "id": "closing",
        "name": "结尾",
        "duration": 70,
        "pattern": [
          "host_to_camera",
          "reaction",
          "end_card"
        ]
      }
    ],
    "packaging": [
      [
        "片头",
        "logo_sting"
      ],
      [
        "片尾",
        "end_card"
      ],
      [
        "花字",
        "zaopianju_yellow"
      ],
      [
        "转场",
        "hard_cut"
      ],
      [
        "BGM",
        "talk_light"
      ],
      [
        "字幕",
        "variety"
      ]
    ]
  },
  {
    "id": "history_talk_5min",
    "name": "历史夜话 · 5 分钟",
    "tagline": "历史夜话：主持人 + 历史人物 AI 分身圆桌，聊有争议的历史",
    "aspect": "16:9",
    "target_duration": 348,
    "accent": "#FF6B6B",
    "segments": [
      {
        "id": "open",
        "name": "片头",
        "duration": 6,
        "pattern": [
          "title_card"
        ]
      },
      {
        "id": "host_open",
        "name": "主持人开场",
        "duration": 42,
        "pattern": [
          "host_to_camera",
          "meme_card",
          "b_roll"
        ]
      },
      {
        "id": "figure_a",
        "name": "历史人物 A 自述",
        "duration": 70,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction",
          "b_roll"
        ]
      },
      {
        "id": "figure_b",
        "name": "历史人物 B 反驳",
        "duration": 70,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction",
          "meme_card"
        ]
      },
      {
        "id": "roundtable",
        "name": "三人圆桌 · 跨时空辩论",
        "duration": 95,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction",
          "meme_card",
          "b_roll"
        ]
      },
      {
        "id": "closing",
        "name": "主持人收束与结尾",
        "duration": 65,
        "pattern": [
          "host_to_camera",
          "reaction",
          "end_card"
        ]
      }
    ],
    "packaging": [
      [
        "片头",
        "logo_sting"
      ],
      [
        "片尾",
        "end_card"
      ],
      [
        "花字",
        "zaopianju_yellow"
      ],
      [
        "转场",
        "hard_cut"
      ],
      [
        "BGM",
        "talk_light"
      ],
      [
        "字幕",
        "variety"
      ]
    ]
  },
  {
    "id": "history_mystery_5min",
    "name": "历史悬案 · 5 分钟",
    "tagline": "历史悬案：情景再现 + 线索推演 + 投票揭晓",
    "aspect": "16:9",
    "target_duration": 359,
    "accent": "#5AC8FA",
    "segments": [
      {
        "id": "open",
        "name": "片头",
        "duration": 6,
        "pattern": [
          "title_card"
        ]
      },
      {
        "id": "case",
        "name": "悬案背景 · 情景再现",
        "duration": 48,
        "pattern": [
          "host_to_camera",
          "b_roll",
          "meme_card"
        ]
      },
      {
        "id": "clue1",
        "name": "线索一 · 史料比对",
        "duration": 80,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "b_roll",
          "meme_card"
        ]
      },
      {
        "id": "clue2",
        "name": "线索二 · 人物动机",
        "duration": 80,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction",
          "b_roll"
        ]
      },
      {
        "id": "deduction",
        "name": "推演与质疑",
        "duration": 75,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction",
          "meme_card"
        ]
      },
      {
        "id": "closing",
        "name": "投票与揭晓",
        "duration": 70,
        "pattern": [
          "host_to_camera",
          "reaction",
          "meme_card",
          "end_card"
        ]
      }
    ],
    "packaging": [
      [
        "片头",
        "logo_sting"
      ],
      [
        "片尾",
        "end_card"
      ],
      [
        "花字",
        "zaopianju_yellow"
      ],
      [
        "转场",
        "hard_cut"
      ],
      [
        "BGM",
        "talk_light"
      ],
      [
        "字幕",
        "variety"
      ]
    ]
  },
  {
    "id": "relic_talk_5min",
    "name": "国宝会说话 · 5 分钟",
    "tagline": "国宝会说话：一件文物一个故事 + 主持讲解 + 花字收口",
    "aspect": "16:9",
    "target_duration": 306,
    "accent": "#EAB308",
    "segments": [
      {
        "id": "open",
        "name": "片头",
        "duration": 6,
        "pattern": [
          "title_card"
        ]
      },
      {
        "id": "opening",
        "name": "开场 · 走进博物馆",
        "duration": 40,
        "pattern": [
          "host_to_camera",
          "b_roll",
          "meme_card"
        ]
      },
      {
        "id": "relic_sword",
        "name": "第一件 · 越王勾践剑",
        "duration": 75,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "reaction",
          "b_roll"
        ]
      },
      {
        "id": "relic_painting",
        "name": "第二件 · 千里江山图",
        "duration": 70,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "b_roll"
        ]
      },
      {
        "id": "relic_bells",
        "name": "第三件 · 曾侯乙编钟",
        "duration": 70,
        "pattern": [
          "host_to_camera",
          "shot_reverse",
          "b_roll"
        ]
      },
      {
        "id": "closing",
        "name": "结尾 · 千年的对话",
        "duration": 45,
        "pattern": [
          "host_to_camera",
          "b_roll",
          "end_card"
        ]
      }
    ],
    "packaging": [
      [
        "片头",
        "logo_sting"
      ],
      [
        "片尾",
        "end_card"
      ],
      [
        "花字",
        "zaopianju_yellow"
      ],
      [
        "转场",
        "hard_cut"
      ],
      [
        "BGM",
        "talk_light"
      ],
      [
        "字幕",
        "variety"
      ]
    ]
  }
];

export function templateLabel(id?: string | null): string {
  const found = TEMPLATES.find((item) => item.id === id);
  return found ? found.name : id || '未指定模式';
}
