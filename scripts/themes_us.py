"""
美股題材（類股輪動）設定：大類、題材、成分股。純資料，不 import 任何第三方套件。

- 每個題材 5–12 檔代表性個股；同一檔可出現在多個題材（例如 NVDA 同時屬於 AI 晶片與七巨頭）。
- tickers 的每一筆是 (代號, 中文名稱, 一句話白話說明)。說明給完全不懂產業的人看。
- etf 是「參考 ETF」：把同一主題的股票包成一檔基金，可當作這個題材的官方參照；沒有合適的就是 None。
- 新上市或剛改名的代號（SNDK、XYZ、B、FLY、CRCL、GLXY、USAR、ALAB、TEM…）若下載失敗，
  build_themes.py 會自動略過並記錄，不影響其他成分股。
"""

PARENTS = [
    {"key": "semis", "name": "半導體", "order": 1},
    {"key": "ai-infra", "name": "AI基建與電力", "order": 2},
    {"key": "software", "name": "軟體與網路", "order": 3},
    {"key": "finance", "name": "金融與加密", "order": 4},
    {"key": "industrial", "name": "工業國防太空", "order": 5},
    {"key": "energy", "name": "能源與原物料", "order": 6},
    {"key": "health", "name": "醫療健康", "order": 7},
    {"key": "consumer", "name": "國際與消費", "order": 8},
]

THEMES = [
    # ------------------------------------------------------------------ 半導體
    {
        "key": "ai-chips",
        "name": "AI晶片",
        "parent": "semis",
        "description": (
            "訓練和執行 AI（像 ChatGPT）需要大量運算，這些公司設計 GPU、客製化 AI 晶片與處理器，"
            "是這一波 AI 熱潮最直接的受惠者。AI 資本支出增減，會最先反映在這一籃子。"
        ),
        "etf": "SMH",
        "tickers": [
            ("NVDA", "輝達", "AI 晶片龍頭，GPU 市佔最高"),
            ("AMD", "超微", "GPU 與伺服器 CPU，輝達的主要對手"),
            ("AVGO", "博通", "替大型雲端公司客製 AI 晶片與網通晶片"),
            ("MRVL", "邁威爾", "資料中心客製晶片與高速傳輸晶片"),
            ("ARM", "安謀", "手機與伺服器晶片的設計藍圖授權"),
            ("QCOM", "高通", "手機晶片龍頭，延伸到 AI PC 與車用"),
            ("INTC", "英特爾", "PC 與伺服器處理器，也做晶圓代工"),
            ("ALAB", "Astera Labs", "AI 伺服器內部的高速連接晶片"),
        ],
    },
    {
        "key": "memory",
        "name": "記憶體與儲存",
        "parent": "semis",
        "description": (
            "AI 伺服器需要大量高速記憶體（HBM、DRAM）和硬碟、快閃儲存來存放資料。"
            "這類股票景氣循環明顯：缺貨漲價時獲利暴增，供過於求時價格崩跌。"
        ),
        "etf": None,
        "tickers": [
            ("MU", "美光", "DRAM 與 HBM 記憶體大廠"),
            ("SNDK", "晟碟 Sandisk", "快閃記憶體（SSD、記憶卡），2025 年從 WDC 分拆"),
            ("WDC", "西部數據", "硬碟大廠"),
            ("STX", "希捷", "硬碟大廠，資料中心大容量硬碟"),
            ("PSTG", "Pure Storage", "企業級全快閃儲存設備"),
            ("NTAP", "NetApp", "企業資料儲存與管理"),
        ],
    },
    {
        "key": "semi-equip",
        "name": "半導體設備",
        "parent": "semis",
        "description": (
            "晶片工廠要蓋產線、買機器才能生產，這些公司就是「賣鏟子的人」：曝光機、蝕刻機、檢測機。"
            "晶圓廠擴產時它們先受惠，景氣一轉弱也最先感受到。"
        ),
        "etf": None,
        "tickers": [
            ("ASML", "艾司摩爾", "全球唯一的極紫外光（EUV）曝光機供應商"),
            ("AMAT", "應用材料", "最大的晶片製造設備商"),
            ("LRCX", "科林研發", "晶片蝕刻與沉積設備"),
            ("KLAC", "科磊", "晶片檢測與量測設備"),
            ("TER", "泰瑞達", "晶片測試設備，也做機器手臂"),
            ("ONTO", "Onto Innovation", "先進封裝的檢測設備"),
            ("ENTG", "英特格", "晶片製程用的特殊材料與過濾"),
            ("AMKR", "艾克爾", "晶片封裝測試代工"),
        ],
    },
    {
        "key": "foundry",
        "name": "晶圓代工與封測",
        "parent": "semis",
        "description": (
            "很多晶片公司只負責設計，實際製造交給「代工廠」。台積電是全球最重要的一家，"
            "封測廠則負責把晶片切割、包裝、測試。這一籃子反映全球晶片的實際出貨熱度。"
        ),
        "etf": None,
        "tickers": [
            ("TSM", "台積電 ADR", "全球最大晶圓代工，AI 晶片幾乎都靠它製造"),
            ("GFS", "格羅方德", "美國成熟製程晶圓代工"),
            ("UMC", "聯電 ADR", "台灣成熟製程晶圓代工"),
            ("ASX", "日月光 ADR", "全球最大封裝測試廠"),
            ("AMKR", "艾克爾", "美國封測大廠"),
            ("INTC", "英特爾", "也投入晶圓代工，美國政府持股"),
        ],
    },
    {
        "key": "analog-auto",
        "name": "類比與車用晶片",
        "parent": "semis",
        "description": (
            "這類晶片負責電源管理、感測、馬達控制，用在汽車、工廠、家電。"
            "景氣循環和 AI 不同步，通常要看汽車與工業需求是否回溫。"
        ),
        "etf": None,
        "tickers": [
            ("TXN", "德州儀器", "類比晶片龍頭，工業與汽車為主"),
            ("ADI", "亞德諾", "高階類比與感測晶片"),
            ("NXPI", "恩智浦", "車用晶片大廠"),
            ("ON", "安森美", "車用與電動車功率半導體"),
            ("MCHP", "微芯", "微控制器（家電、工業的小腦）"),
            ("STM", "意法半導體 ADR", "歐洲車用與工業晶片大廠"),
            ("MPWR", "芯源系統 MPS", "電源管理晶片，AI 伺服器供電受惠"),
        ],
    },
    # ------------------------------------------------------------------ AI基建與電力
    {
        "key": "optical",
        "name": "光通訊",
        "parent": "ai-infra",
        "description": (
            "AI 資料中心裡成千上萬顆晶片要互相傳資料，光纖與光模組負責「用光傳輸」，速度快、耗電低。"
            "AI 機房越蓋越大，需求就跟著成長，股價波動也很大。"
        ),
        "etf": None,
        "tickers": [
            ("COHR", "相干 Coherent", "光學元件與雷射，光模組核心零件"),
            ("LITE", "Lumentum", "光通訊用雷射晶片"),
            ("CIEN", "Ciena", "電信與資料中心的光傳輸設備"),
            ("FN", "Fabrinet", "光模組代工，輝達供應鏈"),
            ("AAOI", "應用光電", "資料中心光收發模組"),
            ("CRDO", "Credo", "高速連接線纜與晶片"),
            ("GLW", "康寧", "光纖與玻璃材料大廠"),
        ],
    },
    {
        "key": "ai-servers",
        "name": "AI伺服器與網通",
        "parent": "ai-infra",
        "description": (
            "AI 晶片要裝進伺服器、接上網路、加上散熱與供電才能運作。這些公司組裝伺服器、"
            "賣交換器與散熱電力設備，是 AI 機房建置的「整機」與「周邊」。"
        ),
        "etf": None,
        "tickers": [
            ("SMCI", "美超微", "AI 伺服器組裝與液冷方案"),
            ("DELL", "戴爾", "企業 AI 伺服器出貨大廠"),
            ("HPE", "慧與", "伺服器與網路設備"),
            ("ANET", "Arista", "資料中心高速網路交換器"),
            ("CLS", "Celestica", "AI 伺服器與網通設備代工"),
            ("VRT", "維諦 Vertiv", "資料中心散熱與供電設備"),
            ("JBL", "捷普", "電子產品代工，含 AI 伺服器"),
        ],
    },
    {
        "key": "power-grid",
        "name": "AI用電與電網",
        "parent": "ai-infra",
        "description": (
            "AI 機房非常耗電，美國電力不夠用。發電廠、輸配電設備、電力工程公司因此成為熱門題材："
            "核電與天然氣發電賣電給資料中心，電網設備商則排單排到好幾年後。"
        ),
        "etf": "GRID",
        "tickers": [
            ("GEV", "GE 凡爾諾", "燃氣渦輪機與電網設備"),
            ("VST", "Vistra", "發電公司，直接賣電給資料中心"),
            ("CEG", "星座能源", "美國最大核電業者"),
            ("NRG", "NRG 能源", "發電與零售電力公司"),
            ("TLN", "Talen", "核電與發電，供電給 AI 機房"),
            ("ETN", "伊頓", "電力管理與配電設備"),
            ("PWR", "Quanta Services", "電網與電力工程承包商"),
        ],
    },
    {
        "key": "nuclear",
        "name": "核能與鈾",
        "parent": "ai-infra",
        "description": (
            "核能是不排碳又穩定的電力，被視為 AI 時代的重要電源。這個題材包含挖鈾礦的公司、"
            "濃縮鈾燃料，以及正在開發「小型模組化核反應爐」的新創，多數公司尚未獲利，波動非常大。"
        ),
        "etf": "URA",
        "tickers": [
            ("CCJ", "Cameco", "全球最大鈾礦商之一"),
            ("OKLO", "Oklo", "小型核反應爐新創，尚無營收"),
            ("SMR", "NuScale", "小型模組化反應爐設計商"),
            ("LEU", "Centrus", "濃縮鈾燃料供應商"),
            ("BWXT", "BWX 科技", "美國海軍核動力與核燃料零件"),
            ("UEC", "鈾能源", "美國鈾礦開採商"),
            ("NXE", "NexGen", "加拿大高品位鈾礦開發商"),
            ("NNE", "Nano Nuclear", "微型反應爐新創，尚無營收"),
        ],
    },
    # ------------------------------------------------------------------ 軟體與網路
    {
        "key": "megacap",
        "name": "七巨頭",
        "parent": "software",
        "description": (
            "蘋果、微軟、谷歌、亞馬遜、Meta、輝達、特斯拉，是美股市值最大的七家科技公司，"
            "權重高到會左右整個 S&P 500 的漲跌。看這一籃子等於看大型科技股的整體氣氛。"
        ),
        "etf": "MAGS",
        "tickers": [
            ("AAPL", "蘋果", "iPhone、Mac 與服務"),
            ("MSFT", "微軟", "Windows、Office、Azure 雲端"),
            ("GOOGL", "谷歌 Alphabet", "搜尋、YouTube、雲端與 AI"),
            ("AMZN", "亞馬遜", "電商與全球最大雲端 AWS"),
            ("META", "Meta", "Facebook、Instagram、WhatsApp"),
            ("NVDA", "輝達", "AI 晶片龍頭"),
            ("TSLA", "特斯拉", "電動車、儲能與自駕"),
        ],
    },
    {
        "key": "cloud-saas",
        "name": "雲端軟體",
        "parent": "software",
        "description": (
            "企業用月費租用的軟體（SaaS），例如客戶管理、人事、資料庫、協作工具。"
            "市場近年擔心 AI 會取代部分軟體，所以這個題材常常和 AI 晶片走相反方向。"
        ),
        "etf": "IGV",
        "tickers": [
            ("CRM", "Salesforce", "企業客戶關係管理軟體"),
            ("NOW", "ServiceNow", "企業工作流程自動化"),
            ("SNOW", "Snowflake", "雲端資料倉儲"),
            ("DDOG", "Datadog", "雲端系統監控"),
            ("MDB", "MongoDB", "雲端資料庫"),
            ("WDAY", "Workday", "企業人資與財務軟體"),
            ("TEAM", "Atlassian", "團隊協作與專案管理工具"),
            ("HUBS", "HubSpot", "中小企業行銷與銷售軟體"),
            ("ORCL", "甲骨文", "資料庫與雲端，AI 機房大單題材"),
        ],
    },
    {
        "key": "cyber",
        "name": "資安",
        "parent": "software",
        "description": (
            "保護企業網路、電腦與雲端不被駭客攻擊的公司。資安是企業「不得不花」的預算，"
            "景氣差時也相對不容易被砍；AI 帶來的新型攻擊又增加了需求。"
        ),
        "etf": "CIBR",
        "tickers": [
            ("CRWD", "CrowdStrike", "終端裝置與雲端資安"),
            ("PANW", "派拓網路", "防火牆與整合型資安平台"),
            ("FTNT", "Fortinet", "防火牆與網路安全設備"),
            ("ZS", "Zscaler", "雲端網路安全閘道"),
            ("NET", "Cloudflare", "網站加速與防護"),
            ("OKTA", "Okta", "帳號登入與身分驗證"),
            ("S", "SentinelOne", "AI 驅動的終端防護"),
            ("CHKP", "Check Point", "老牌防火牆廠商（以色列）"),
        ],
    },
    {
        "key": "ai-software",
        "name": "AI應用軟體",
        "parent": "software",
        "description": (
            "把 AI 做成企業或政府真的在用的產品：資料分析、廣告投放、語音助理、流程自動化。"
            "這個題材用「AI 概念股」的熱度定價，漲跌幅度大，投機成分也高。"
        ),
        "etf": None,
        "tickers": [
            ("PLTR", "Palantir", "政府與企業的 AI 資料分析平台"),
            ("APP", "AppLovin", "AI 手遊廣告投放平台"),
            ("AI", "C3.ai", "企業 AI 軟體，長期虧損"),
            ("SOUN", "SoundHound", "語音 AI 助理"),
            ("PATH", "UiPath", "流程自動化機器人軟體"),
            ("TEM", "Tempus AI", "用 AI 分析基因與醫療資料"),
            ("BBAI", "BigBear.ai", "國防與政府 AI 分析"),
        ],
    },
    {
        "key": "internet-media",
        "name": "網路與串流媒體",
        "parent": "software",
        "description": (
            "靠廣告、訂閱與用戶時間賺錢的網路平台：社群、影音串流、遊戲、音樂。"
            "營收和廣告景氣、用戶成長、內容爆款有關。"
        ),
        "etf": None,
        "tickers": [
            ("META", "Meta", "Facebook、Instagram"),
            ("NFLX", "網飛", "影音串流龍頭"),
            ("SPOT", "Spotify", "音樂串流"),
            ("RDDT", "Reddit", "論壇社群，廣告與 AI 資料授權"),
            ("RBLX", "Roblox", "兒少為主的線上遊戲平台"),
            ("TTWO", "Take-Two", "GTA 系列遊戲發行商"),
            ("ROKU", "Roku", "電視串流平台與廣告"),
            ("DIS", "迪士尼", "電影、串流、樂園"),
        ],
    },
    {
        "key": "ecommerce",
        "name": "電商與平台經濟",
        "parent": "software",
        "description": (
            "網路購物、外送、叫車、訂房這些「在手機上下單」的平台。看的是消費者願不願意花錢，"
            "對景氣與利率比較敏感。"
        ),
        "etf": None,
        "tickers": [
            ("AMZN", "亞馬遜", "全球最大電商"),
            ("SHOP", "Shopify", "幫商家開網路商店的軟體"),
            ("MELI", "MercadoLibre", "拉丁美洲電商與支付"),
            ("CPNG", "酷澎 Coupang", "南韓最大電商"),
            ("DASH", "DoorDash", "美國外送平台龍頭"),
            ("UBER", "優步", "叫車與外送平台"),
            ("ABNB", "愛彼迎", "民宿訂房平台"),
        ],
    },
    # ------------------------------------------------------------------ 金融與加密
    {
        "key": "crypto",
        "name": "加密貨幣概念",
        "parent": "finance",
        "description": (
            "跟比特幣等加密貨幣連動的股票：交易所、挖礦公司、囤幣公司，以及比特幣現貨 ETF。"
            "基本上是「槓桿版的幣價」，比特幣漲時漲更多，跌時也跌更多。"
        ),
        "etf": "IBIT",
        "tickers": [
            ("COIN", "Coinbase", "美國最大合法加密貨幣交易所"),
            ("MSTR", "Strategy", "把公司現金拿去大量買比特幣"),
            ("HOOD", "Robinhood", "散戶券商，加密交易占比高"),
            ("MARA", "Marathon Digital", "比特幣挖礦公司"),
            ("RIOT", "Riot Platforms", "比特幣挖礦公司"),
            ("CLSK", "CleanSpark", "比特幣挖礦公司"),
            ("CRCL", "Circle", "穩定幣 USDC 發行商，2025 年上市"),
            ("GLXY", "Galaxy Digital", "加密資產金融與資料中心"),
        ],
    },
    {
        "key": "fintech",
        "name": "金融科技與支付",
        "parent": "finance",
        "description": (
            "刷卡、行動支付、網路借貸、數位銀行。傳統卡組織很穩健，新創金融科技則受利率和消費信用影響較大。"
        ),
        "etf": "FINX",
        "tickers": [
            ("V", "Visa", "全球最大信用卡網路"),
            ("MA", "萬事達卡", "全球第二大信用卡網路"),
            ("PYPL", "PayPal", "線上支付與 Venmo"),
            ("XYZ", "Block", "Square 刷卡機與 Cash App（原代號 SQ）"),
            ("SOFI", "SoFi", "數位銀行與學貸、個人貸款"),
            ("AFRM", "Affirm", "先買後付（分期）平台"),
            ("TOST", "Toast", "餐廳收銀與支付系統"),
            ("HOOD", "Robinhood", "散戶券商與加密交易"),
        ],
    },
    # ------------------------------------------------------------------ 工業國防太空
    {
        "key": "space",
        "name": "太空",
        "parent": "industrial",
        "description": (
            "火箭發射、衛星、月球探測與太空基礎設施。多為成長型新創公司，營收還小，"
            "股價靠未來想像與政府訂單，漲跌很劇烈。"
        ),
        "etf": "UFO",
        "tickers": [
            ("RKLB", "火箭實驗室", "小型火箭發射與衛星製造"),
            ("ASTS", "AST SpaceMobile", "衛星直連手機的寬頻服務"),
            ("LUNR", "Intuitive Machines", "月球登陸器，NASA 承包商"),
            ("PL", "Planet Labs", "地球觀測衛星影像"),
            ("RDW", "Redwire", "太空基礎設施與零組件"),
            ("IRDM", "Iridium", "全球衛星通訊"),
            ("FLY", "Firefly Aerospace", "火箭與月球登陸器，2025 年上市"),
            ("SATS", "EchoStar", "衛星通訊與頻譜資產"),
        ],
    },
    {
        "key": "defense",
        "name": "國防軍工",
        "parent": "industrial",
        "description": (
            "戰機、飛彈、軍艦、雷達的製造商，客戶是各國政府。國防預算增加、地緣衝突升溫時受惠，"
            "盈利穩定、波動比科技股小。"
        ),
        "etf": "ITA",
        "tickers": [
            ("LMT", "洛克希德馬丁", "F-35 戰機與飛彈"),
            ("NOC", "諾斯洛普格魯曼", "B-21 轟炸機與太空系統"),
            ("RTX", "雷神", "飛彈、雷達與民航引擎"),
            ("GD", "通用動力", "潛艦、戰車與灣流商務機"),
            ("LHX", "L3哈里斯", "軍用通訊與電子系統"),
            ("HII", "亨廷頓英格爾斯", "美國海軍艦艇建造"),
            ("KTOS", "Kratos", "軍用無人機與高超音速測試"),
            ("AVAV", "AeroVironment", "小型軍用無人機"),
        ],
    },
    {
        "key": "drones-evtol",
        "name": "無人機與飛行車",
        "parent": "industrial",
        "description": (
            "兩條故事線：軍用或商用無人機，以及「電動垂直起降飛行器（eVTOL）」，也就是未來的飛行計程車。"
            "多數仍在取得認證或量產初期，消息面影響大。"
        ),
        "etf": None,
        "tickers": [
            ("JOBY", "Joby Aviation", "電動飛行計程車，已進入認證"),
            ("ACHR", "Archer Aviation", "電動飛行計程車"),
            ("AVAV", "AeroVironment", "小型軍用無人機"),
            ("KTOS", "Kratos", "軍用無人機"),
            ("RCAT", "Red Cat", "軍用小型無人機"),
            ("ONDS", "Ondas", "無人機與工業無線網路"),
            ("UMAC", "Unusual Machines", "無人機零組件"),
            ("EH", "億航 EHang", "中國載人無人機（自動駕駛飛行器）"),
        ],
    },
    {
        "key": "robotics",
        "name": "機器人與自動化",
        "parent": "industrial",
        "description": (
            "工廠機械手臂、手術機器人、倉儲自動化與視覺系統，也包含人形機器人的概念。"
            "AI 讓機器人更聰明，是被看好的長期趨勢。"
        ),
        "etf": "BOTZ",
        "tickers": [
            ("ISRG", "直覺外科", "達文西手術機器人"),
            ("ROK", "羅克韋爾自動化", "工廠自動化控制設備"),
            ("SYM", "Symbotic", "倉儲物流機器人，沃爾瑪供應商"),
            ("TER", "泰瑞達", "晶片測試設備，也有協作機器手臂"),
            ("ZBRA", "斑馬技術", "條碼掃描與物流自動化"),
            ("CGNX", "康耐視", "工廠機器視覺系統"),
            ("SERV", "Serve Robotics", "人行道送餐機器人"),
        ],
    },
    {
        "key": "quantum",
        "name": "量子電腦",
        "parent": "industrial",
        "description": (
            "量子電腦用完全不同的原理運算，理論上能解決傳統電腦做不到的問題。"
            "目前還在研發階段，純量子公司幾乎沒有獲利，股價靠題材與資金情緒推動，風險很高。"
        ),
        "etf": "QTUM",
        "tickers": [
            ("IONQ", "IonQ", "離子阱量子電腦"),
            ("RGTI", "Rigetti", "超導量子電腦"),
            ("QBTS", "D-Wave", "量子退火電腦"),
            ("QUBT", "Quantum Computing Inc.", "光子量子技術新創"),
            ("IBM", "IBM", "老牌科技巨頭，量子研發領先"),
            ("GOOGL", "谷歌 Alphabet", "量子晶片 Willow 研發"),
            ("HON", "漢威聯合", "持有量子電腦公司 Quantinuum"),
        ],
    },
    # ------------------------------------------------------------------ 能源與原物料
    {
        "key": "ev-battery",
        "name": "電動車與電池",
        "parent": "energy",
        "description": (
            "電動車製造商與電池原料（鋰）供應商。這幾年受高利率、價格戰與補助政策影響，"
            "走勢和電動車銷量、各國補助、鋰價高度相關。"
        ),
        "etf": "LIT",
        "tickers": [
            ("TSLA", "特斯拉", "電動車龍頭，也做儲能與自駕"),
            ("RIVN", "Rivian", "美國電動皮卡與休旅車"),
            ("LCID", "Lucid", "高端電動轎車"),
            ("LI", "理想汽車 ADR", "中國增程式電動休旅車"),
            ("NIO", "蔚來 ADR", "中國高端電動車與換電站"),
            ("XPEV", "小鵬汽車 ADR", "中國電動車與智駕"),
            ("ALB", "雅寶", "全球最大鋰生產商之一"),
            ("QS", "QuantumScape", "固態電池研發新創"),
        ],
    },
    {
        "key": "solar",
        "name": "太陽能與綠能",
        "parent": "energy",
        "description": (
            "太陽能板、逆變器與燃料電池。對利率與政策補助（美國的稅收抵免）非常敏感，"
            "政策消息出來時往往一天大漲或大跌。"
        ),
        "etf": "TAN",
        "tickers": [
            ("FSLR", "第一太陽能", "美國太陽能板製造商"),
            ("ENPH", "Enphase", "家用太陽能微型逆變器"),
            ("NXT", "Nextracker", "太陽能電廠追日支架"),
            ("RUN", "Sunrun", "家用太陽能租賃"),
            ("SEDG", "SolarEdge", "太陽能逆變器"),
            ("ARRY", "Array Technologies", "太陽能追日系統"),
            ("BE", "Bloom Energy", "燃料電池，供電給資料中心"),
        ],
    },
    {
        "key": "gold-miners",
        "name": "黃金礦業",
        "parent": "energy",
        "description": (
            "金礦公司的獲利跟金價高度連動，而且有槓桿效果：金價漲 10%，礦業股常常漲更多。"
            "當市場擔心通膨、美元走弱或地緣風險時，常成為避險資金的去處。"
        ),
        "etf": "GDX",
        "tickers": [
            ("NEM", "紐蒙特", "全球最大金礦公司"),
            ("AEM", "鷹牌礦業", "加拿大大型金礦商"),
            ("B", "巴里克", "全球大型金礦與銅礦商（原代號 GOLD）"),
            ("WPM", "惠頓貴金屬", "預付款買金銀的權利金公司"),
            ("FNV", "Franco-Nevada", "黃金權利金公司"),
            ("KGC", "金羅斯", "大型金礦商"),
            ("AU", "安格魯黃金", "南非起家的全球金礦商"),
            ("HMY", "和諧黃金", "南非金礦商"),
        ],
    },
    {
        "key": "critical-minerals",
        "name": "銅與稀土",
        "parent": "energy",
        "description": (
            "銅是電網、電動車、資料中心都需要的基礎金屬；稀土是做強力磁鐵、飛彈、電動馬達的關鍵原料，"
            "中國掌握大部分供應，所以美國積極扶植本土廠商。"
        ),
        "etf": "REMX",
        "tickers": [
            ("FCX", "自由港", "全球最大上市銅礦商"),
            ("SCCO", "南方銅業", "秘魯、墨西哥銅礦商"),
            ("HBM", "Hudbay", "加拿大銅礦商"),
            ("MP", "MP Materials", "美國唯一大型稀土礦，國防部入股"),
            ("USAR", "USA Rare Earth", "美國稀土磁鐵新創"),
            ("UUUU", "Energy Fuels", "鈾與稀土加工商"),
            ("LAC", "Lithium Americas", "美國鋰礦開發商"),
        ],
    },
    # ------------------------------------------------------------------ 醫療健康
    {
        "key": "biotech",
        "name": "生技製藥",
        "parent": "health",
        "description": (
            "研發新藥的公司。靠臨床試驗結果和 FDA 核准定生死，單一消息就可能大漲大跌。"
            "大型生技股現金流穩定，小型生技股則像買樂透。"
        ),
        "etf": "XBI",
        "tickers": [
            ("VRTX", "福泰", "囊性纖維化藥物龍頭"),
            ("REGN", "再生元", "眼科與免疫藥物"),
            ("AMGN", "安進", "大型生技，也在開發減重藥"),
            ("GILD", "吉利德", "HIV 與肝炎藥物"),
            ("BIIB", "百健", "阿茲海默症與神經藥物"),
            ("ALNY", "Alnylam", "RNA 干擾藥物"),
            ("MRNA", "莫德納", "mRNA 疫苗與藥物"),
        ],
    },
    {
        "key": "glp1",
        "name": "減重藥（GLP-1）",
        "parent": "health",
        "description": (
            "GLP-1 是近年最紅的藥物類別，可以降血糖也能明顯減重，市場規模預期達千億美元。"
            "禮來和諾和諾德是兩大龍頭，其他公司在搶第二代產品或經銷。"
        ),
        "etf": None,
        "tickers": [
            ("LLY", "禮來", "Zepbound/Mounjaro 的製造商"),
            ("NVO", "諾和諾德 ADR", "Wegovy/Ozempic 的製造商"),
            ("VKTX", "Viking", "開發口服與注射減重藥的新創"),
            ("HIMS", "Hims & Hers", "線上診所，賣減重藥處方"),
            ("AMGN", "安進", "正在開發減重新藥"),
            ("PFE", "輝瑞", "收購 Metsera 切入減重藥"),
            ("GPCR", "Structure", "開發口服 GLP-1 的新創"),
        ],
    },
    {
        "key": "medtech",
        "name": "醫療器材",
        "parent": "health",
        "description": (
            "手術機器人、心臟支架、血糖監測器、關節置換等醫療設備。需求來自人口老化，"
            "比較穩定、屬於防禦型題材。"
        ),
        "etf": "IHI",
        "tickers": [
            ("ISRG", "直覺外科", "達文西手術機器人"),
            ("SYK", "史賽克", "骨科關節與手術設備"),
            ("BSX", "波士頓科學", "心臟與血管微創器材"),
            ("MDT", "美敦力", "心律調節器與糖尿病設備"),
            ("DXCM", "德康", "連續血糖監測器"),
            ("EW", "愛德華生命科學", "人工心臟瓣膜"),
            ("ABT", "亞培", "醫療器材、診斷與營養品"),
        ],
    },
    # ------------------------------------------------------------------ 國際與消費
    {
        "key": "china-adr",
        "name": "中國概念股",
        "parent": "consumer",
        "description": (
            "在美國上市的中國大型公司（電商、搜尋、遊戲、旅遊）。走勢除了公司本身，"
            "還受中國政策、中美關係與人民幣影響，常因為政策消息大幅跳動。"
        ),
        "etf": "KWEB",
        "tickers": [
            ("BABA", "阿里巴巴", "中國最大電商與雲端"),
            ("PDD", "拼多多", "低價電商與 Temu 母公司"),
            ("JD", "京東", "中國自營電商與物流"),
            ("BIDU", "百度", "中國搜尋引擎與自駕計程車"),
            ("NTES", "網易", "中國線上遊戲大廠"),
            ("TCOM", "攜程", "中國最大旅遊訂票平台"),
            ("BILI", "嗶哩嗶哩", "中國年輕人影音社群平台"),
        ],
    },
]

BENCHMARK = "SPY"
