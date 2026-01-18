import requests
import socket
import struct
import sqlite3
import concurrent.futures
import time
import uuid
import re 
import ipaddress # <--- Added for IP Math
from datetime import datetime, timedelta

# --- Configuration ---
STEAM_KEY = "xxxx"
APP_ID = 232090
API_URL = f"https://api.steampowered.com/IGameServersService/GetServerList/v1/?key={STEAM_KEY}&limit=50000&filter=\\appid\\{APP_ID}"
DB_FILE = r"C:\apps\Webapp\kf2_panopticon_v3_star.db"

# The Narcissus Shim
LOCAL_LOOPBACK_IP = "127.0.0.1" 

A2S_INFO = b"\xff\xff\xff\xff\x54\x53\x6f\x75\x72\x63\x65\x20\x45\x6e\x67\x69\x6e\x65\x20\x51\x75\x65\x72\x79\x00"
A2S_PLAYER_CHALLENGE = b"\xff\xff\xff\xff\x55\xff\xff\xff\xff"
A2S_PLAYER_HEADER = b"\xff\xff\xff\xff\x55"

MAX_WORKERS = 150
TIMEOUT = 3.0
PRUNE_THRESHOLD = 6

# --- Fix for Python 3.12+ Datetime warnings ---
def adapt_date_iso(val):
    return val.replace(microsecond=0).strftime('%Y-%m-%d %H:%M:%S')

sqlite3.register_adapter(datetime, adapt_date_iso)
# -----------------------------------------------

# --- FACTION INTELLIGENCE MODULE ---
def get_fallback_country(raw_name):
    geo_pattern = r'\b(us|eu|cn|ru|de|au|uk|fr|jp|kr|tw|sg|br|es|th|vn|nl)\b'
    match = re.search(geo_pattern, raw_name, re.IGNORECASE)
    if match:
        return f"Unknown [{match.group(1).upper()}]"
    return "Unknown"

def extract_domain_name(raw_name):
    match = re.search(r'([a-zA-Z0-9-]{2,})\.(com|net|org|tk|ru|de|eu|gg|host|cloud|xyz|info)\b', raw_name.lower())
    if match:
        return match.group(1).title()
    return None

def clean_server_name(raw_name, ip_address):

    if not raw_name: return ip_address
    name = raw_name.lower()

    # --- 0. VIP LIST ---
    VIP_PATTERNS = {
        r'simpleserver': "SimpleServer (TH)",
        r'valeria': "Valeria & Friends",
        r'nekoha': "Nekoha Club",    
        r'\bbaz\b': "BAz",
        r'\bkf-?fr\b': "KF-FR",
        r'\bkf-?br\b': "KF-BR",
        r'jp\s?\|': "JP Server",
        r'\bsg-?servers?\b': "SG-Servers",
        r'\bhuwhyte\b': "Huwhyte",
        r'\btripwire\b': "Tripwire Official",
        r'\bbloodhounds\b': "Bloodhounds",
        r'\bkog\b': "KoG Clan",
        r'\bcyxc\b': "Cyxc",
        r'\bamursk\b': "Amursk",
        r'\bmadhouse\b': "MadHouse",
        r'\bamerica latina brasil\b': "America Latina Brasil",
        r'\blarge\s?farva\b': "Large Farva",
        r'\bpunchguts\b': "Punchguts",
        r'\bspb-?gs\b': "SPB-GS",
        r'\bextreme\s?server\b': "Extreme Server",
        r'\bpowerbits\b': "Powerbits",
        r'\bwilnet\b': "Wilnet Gaming",
        r'\bnerdit\b': "Nerdit",
        r'\bmod-?eu\b': "Mod-EU",
        r'\bnfo(?:servers)?\b': "NFO Servers",
        r'\bdslive\b': "DSLive",
        r'\bzgaming\b': "ZGaming",
        r'\[kr\]\s+public\s+server': "[KR] Public Server",
        r'\bkf2\.eu\b': "KF2.eu SuperPerkTraining",
        r'\btwilight realm\b': "Twilight Realm",
        r'\bthe alley\b': "The Alley",
        r'^cd\s?#\d+': "Legs CD",
        r'the\s?outpost': "The Outpost",
        r'sora-?iro': "Sora-Iro (JP)", 
        
        # --- ASIAN / SPECIAL CHARACTER FACTIONS ---
        r'뽀이뿨이\s?poi': "POI (Korea)",
        r'猛男妙妙屋': "Mengnan (CN)",
        r'烂番茄菜篮子': "Rotten Tomato (CN)",
        r'孤风娱乐': "Gufeng Entertainment",
        r'禁忌边境线': "Forbidden Borderline",
        r'医疗大小姐': "Medical Miss",
        r'土豆服务器': "Potato Server (CN)",
        r'ナツ': "Natsu",
        r'诗人\s?rpg': "Poet RPG",
        r'缅北腰花': "Myanmar Kidney Assoc",
        r'大布笑传': "Dabu Laughing",
        r'柚子': "Youzi",
        r'离离原上咪': "Lili Plain",
    }

    for pattern, faction in VIP_PATTERNS.items():
        if re.search(pattern, name):
            return faction

    # --- 0.5 DOMAIN RESCUE ---
    domain_faction = extract_domain_name(raw_name)
    if domain_faction:
        return domain_faction

    # --- 1. REMOVE WEB TRASH ---
    name = re.sub(r'https?://\S+|www\.\S+|discord\.gg/\S+', '', name)
    name = re.sub(r'\.(com|net|org|tk|ru|de|eu|gg|host|cloud|xyz|info)\b', '', name)
    name = re.sub(r'\bqq\d+\b', '', name)

    # --- 2. REMOVE UUIDs ---
    name = re.sub(r'#[a-f0-9-]{10,}', '', name)

    # --- 2.5 EARLY PIPE SPLIT (ENHANCED) ---
    # Normalize weird pipes to standard pipe
    name = name.replace('¦', '|').replace('｜', '|').replace('│', '|')
    if '|' in name:
        name = name.split('|')[0]

    # --- 3. THE KILL LIST ---
    KILL_PATTERNS = [
        r'\b(us|eu|cn|ru|de|au|uk|fr|jp|kr|tw|sg|br|es|th|vn|nl)\b',
        r'\b(east|west|north|south|central|global|international)\b',
        r'\b(dallas|seattle|miami|chicago|new\s?york|london|tokyo|santiago|montreal|sydney|paris|frankfurt|singapore|los\s?angeles)\b',
        r'\btakeover\b', r'\bstandby\b', r'\bidle\b', r'\bafk\b',
        r'\branked\b', r'\bunranked\b', r'\bwhitelist(?:ed)?\b', r'\bprivate\b',
        r'\bpassword(?:ed)?\b', r'\bpublic\b', r'\bdedicated\b', r'\bofficial\b',
        r'\bby\b', 
        r'\bendless\b', r'\bsurvival\b', r'\bobjective\b', r'\bholdout\b', r'\bversus\b',
        r'\bweekly\b', r'\boutbreak\b', r'\bwave\b', r'\bclassic\b',
        r'\bcd\b', r'\bcontrolled\s?difficulty\b', r'\bprecision\b', r'\bspam\b',
        r'\bzerg\s?mode\b', 
        r'\bhoe\+{0,4}\b', r'\bhell\s?on\s?earth\b', r'\bsuicidal\b', r'\bhard\b',
        r'\bnormal\b', r'\bbeginner\b', r'\bgod\s?mode\b', r'\bdifficulty\b',
        r'\bextreme\b', r'\binsane\b', r'\bvery\b',
        r'\btick(?:rate)?\b', r'\bhz\b', r'\bfps\b', r'\bm\.2\b', r'\bssd\b', r'\bnvme\b',
        r'\blow\s?ping\b', r'\bfast\s?dl\b', r'\bredirect\b', r'\blatency\b',
        r'\bslot\b', r'\bplayer\b', r'\b\d{1,3}p\b',
        r'\bcustom\b', r'\bmap(?:s)?\b', r'\bvanilla\b', r'\bworkshop\b',
        r'\brpg(?:mod)?\b', r'\bzedternal(?:reborn)?\b', r'\breborn\b',
        r'\bno\s?edars?\b', r'\bno\s?qps?\b', r'\bmax\s?spawn\b',
        r'\bweapon(?:s)?\b', r'\bzed(?:s)?\b', r'\bdlc\b', r'\bshared\b',
        r'\bperk(?:s)?\b', r'\blevel(?:s)?\b', r'\blvl\b', r'\bxp\b', r'\bprestige\b',
        r'\bdosh\b', r'\bvault\b', r'\bfriendly\s?fire\b', r'\bff\b',
        r'\brampage(?:mod)?\b', r'\band\s?more\b',
        r'\bkilling floor 2(?: server)?\b', r'\bkf2(?: server)?\b', r'\bserver\b',
        r'\blong\b', r'\bshort\b', r'\bmedium\b', r'\bauto\b', r'\breset\b', r'\bnew\b'
    ]
    
    name = re.sub("|".join(KILL_PATTERNS), ' ', name)

    # --- 4. CLEANUP ---
    name = re.sub(r'#\d+', ' ', name) 
    name = re.sub(r'\b\d+\b', ' ', name) 
    name = re.sub(r'[^\w\s]', ' ', name) 
    name = re.sub(r'\s+', ' ', name).strip()

    # --- 5. THE FAILSAFE ---
    if len(name) < 2:
        fallback = get_fallback_country(raw_name)
        if fallback != "Unknown":
            return fallback
        return f"{ip_address}"

    return name.title()

def resolve_geo_db(conn, ip_str):
    """Resolves IP to City, Code using DB ip_ranges. Verifies IP is within the range."""
    try:
        ip_int = int(ipaddress.IPv4Address(ip_str))
        # We still search by ip_to for the index seek speed, but we select ip_from to verify.
        cur = conn.execute("""
            SELECT city_name, country_code, ip_from
            FROM ip_ranges 
            WHERE ip_to >= ? 
            ORDER BY ip_to ASC 
            LIMIT 1
        """, (ip_int,))
        row = cur.fetchone()
        
        # CRITICAL CHECK: Ensure the IP is actually inside the range
        if row:
            range_from = row[2]
            if ip_int < range_from:
                return "Unknown" # It fell into a gap before this range
            
            # If we are here, ip_from <= ip_int <= ip_to
            if row[0] and row[1]:
                return f"{row[0]}, {row[1]}"
            elif row[1]:
                return row[1]
                
    except Exception as e:
        print(f"[!] Geo Error: {e}") # helpful to see if the DB lock is biting you
        pass
    return "Unknown"
    
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()

        # -------------------------
        # DIMENSION TABLES
        # -------------------------

        cur.execute("""
        CREATE TABLE IF NOT EXISTS dim_maps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS dim_players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            real_name TEXT
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS dim_servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address TEXT NOT NULL,
            query_port INTEGER NOT NULL,
            game_port INTEGER,
            name TEXT,
            current_map_id INTEGER,
            player_count INTEGER DEFAULT 0,
            map_start DATETIME,
            last_seen DATETIME,
            current_session_uuid TEXT,
            operator_name TEXT,
            location TEXT,
            frozen_since DATETIME,
            ingest_disabled INTEGER DEFAULT 0,
            UNIQUE(ip_address, query_port)
        )
        """)

        # -------------------------
        # FACT TABLES
        # -------------------------

        cur.execute("""
        CREATE TABLE IF NOT EXISTS fact_active (
            server_id INTEGER,
            player_id INTEGER,
            map_id INTEGER,
            score INTEGER,
            duration REAL,
            first_seen DATETIME,
            last_seen DATETIME,
            session_uuid TEXT,
            calculated_duration REAL DEFAULT 0,
            last_score_change DATETIME,
            PRIMARY KEY (server_id, player_id)
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS fact_global_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_time DATETIME,
            active_servers INTEGER,
            active_players INTEGER
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS fact_history (
            id INTEGER,
            server_id INTEGER,
            player_id INTEGER,
            map_id INTEGER,
            final_score INTEGER,
            total_time REAL,
            session_start NUM,
            session_end NUM,
            session_uuid TEXT,
            calculated_duration INTEGER
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS fact_server_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER,
            map_id INTEGER,
            session_start DATETIME,
            session_end DATETIME,
            reason TEXT,
            session_uuid TEXT,
            calculated_duration INTEGER DEFAULT 0
        )
        """)

        # -------------------------
        # ROLLUP TABLES
        # -------------------------

        cur.execute("""
        CREATE TABLE IF NOT EXISTS fact_map_daily (
            day DATE,
            map_id INTEGER,
            session_count INTEGER NOT NULL,
            total_seconds INTEGER NOT NULL,
            PRIMARY KEY (day, map_id)
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS fact_operator_daily (
            day DATE,
            operator_name TEXT,
            server_count INTEGER NOT NULL,
            unique_players INTEGER NOT NULL,
            total_playtime_seconds INTEGER NOT NULL,
            last_contact DATETIME,
            PRIMARY KEY (day, operator_name)
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS fact_operator_player_daily (
            day DATE NOT NULL,
            operator_name TEXT NOT NULL,
            player_id INTEGER NOT NULL,
            PRIMARY KEY (day, operator_name, player_id)
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS fact_player_daily (
            day DATE,
            player_id INTEGER,
            session_count INTEGER NOT NULL,
            total_seconds INTEGER NOT NULL,
            PRIMARY KEY (day, player_id)
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS fact_server_daily (
            day DATE,
            server_id INTEGER,
            session_count INTEGER NOT NULL,
            total_seconds INTEGER NOT NULL,
            PRIMARY KEY (day, server_id)
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS fact_traffic_daily (
            day DATE PRIMARY KEY,
            unique_players INTEGER NOT NULL
        )
        """)

        # -------------------------
        # GEO / META TABLES
        # -------------------------

        cur.execute("""
        CREATE TABLE IF NOT EXISTS ip_ranges (
            ip_from INTEGER,
            ip_to INTEGER,
            country_code TEXT,
            country_name TEXT,
            region_name TEXT,
            city_name TEXT
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS ipaddress (
            ipaddress TEXT,
            reserved TEXT,
            continentcode TEXT,
            continentname TEXT,
            contrycode TEXT,
            countryname TEXT,
            statecode TEXT,
            statename TEXT,
            city TEXT,
            postalcode TEXT,
            isp TEXT,
            asn INTEGER
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS meta_kv (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """)

        # -------------------------
        # VISIBILITY TABLES
        # -------------------------

        cur.execute("""
        CREATE TABLE IF NOT EXISTS player_visibility (
            player_id INTEGER PRIMARY KEY,
            hidden INTEGER NOT NULL DEFAULT 1,
            reason TEXT,
            added_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS server_visibility (
            server_id INTEGER PRIMARY KEY,
            hidden INTEGER NOT NULL DEFAULT 1,
            reason TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # -------------------------
        # INDICES (VERBATIM)
        # -------------------------

        indices = [
            "CREATE INDEX IF NOT EXISTS idx_active_last_seen ON fact_active(last_seen)",
            "CREATE INDEX IF NOT EXISTS idx_active_server_score ON fact_active(server_id, score DESC)",
            "CREATE INDEX IF NOT EXISTS idx_dim_players_name ON dim_players(name)",
            "CREATE INDEX IF NOT EXISTS idx_dim_servers_last_seen ON dim_servers(last_seen)",
            "CREATE INDEX IF NOT EXISTS idx_dim_servers_name ON dim_servers(name)",
            "CREATE INDEX IF NOT EXISTS idx_dim_servers_operator_id ON dim_servers(operator_name, id)",
            "CREATE INDEX IF NOT EXISTS idx_dim_servers_operator_players ON dim_servers(operator_name, player_count)",
            "CREATE INDEX IF NOT EXISTS idx_dim_servers_player_count ON dim_servers(player_count)",
            "CREATE INDEX IF NOT EXISTS idx_fact_global_stats_time ON fact_global_stats(scan_time)",
            "CREATE INDEX IF NOT EXISTS idx_fact_history_operator_player ON fact_history(server_id, player_id, calculated_duration)",
            "CREATE INDEX IF NOT EXISTS idx_fact_history_player_duration ON fact_history(player_id, calculated_duration)",
            "CREATE INDEX IF NOT EXISTS idx_fact_history_player_time ON fact_history(player_id, session_start DESC)",
            "CREATE INDEX IF NOT EXISTS idx_fact_history_server_player ON fact_history(server_id, player_id)",
            "CREATE INDEX IF NOT EXISTS idx_fact_history_server_time ON fact_history(server_id, session_start DESC)",
            "CREATE INDEX IF NOT EXISTS idx_fact_history_session_player ON fact_history(session_uuid, player_id)",
            "CREATE INDEX IF NOT EXISTS idx_fact_history_session_uuid ON fact_history(session_uuid)",
            "CREATE INDEX IF NOT EXISTS idx_fact_map_daily_map_day ON fact_map_daily(map_id, day)",
            "CREATE INDEX IF NOT EXISTS idx_fact_operator_daily_day_operator ON fact_operator_daily(day, operator_name)",
            "CREATE INDEX IF NOT EXISTS idx_fact_operator_daily_operator_day ON fact_operator_daily(operator_name, day)",
            "CREATE INDEX IF NOT EXISTS idx_fact_operator_player_daily_day_operator_player ON fact_operator_player_daily(day, operator_name, player_id)",
            "CREATE INDEX IF NOT EXISTS idx_fact_operator_player_daily_operator_day ON fact_operator_player_daily(operator_name, day)",
            "CREATE INDEX IF NOT EXISTS idx_fact_operator_player_daily_operator_player ON fact_operator_player_daily(operator_name, player_id)",
            "CREATE INDEX IF NOT EXISTS idx_fact_player_daily_day_player ON fact_player_daily(day, player_id)",
            "CREATE INDEX IF NOT EXISTS idx_fact_player_daily_player_day ON fact_player_daily(player_id, day)",
            "CREATE INDEX IF NOT EXISTS idx_fact_server_daily_server_day ON fact_server_daily(server_id, day)",
            "CREATE INDEX IF NOT EXISTS idx_fact_traffic_daily_day ON fact_traffic_daily(day)",
            "CREATE INDEX IF NOT EXISTS idx_ip_ranges_to ON ip_ranges(ip_to)",
            "CREATE INDEX IF NOT EXISTS idx_server_operator ON dim_servers(operator_name)",
            "CREATE INDEX IF NOT EXISTS idx_server_visibility_server_hidden ON server_visibility(server_id, hidden)",
            "CREATE INDEX IF NOT EXISTS idx_player_visibility_player ON player_visibility(player_id, hidden)",
            "CREATE INDEX IF NOT EXISTS idx_srv_hist_server ON fact_server_history(server_id)",
            "CREATE INDEX IF NOT EXISTS idx_srvhist_server_range ON fact_server_history(server_id, session_start, session_end)"
        ]

        for stmt in indices:
            cur.execute(stmt)

        conn.commit()


# --- Parsing ---
def read_string(data, pos):
    try:
        end = data.find(b'\x00', pos)
        if end == -1: return "", pos
        return data[pos:end].decode('utf-8', errors='ignore'), end + 1
    except: return "Unknown", pos + 1

def parse_iso_time(time_str):
    try:
        if not time_str:
            return datetime.utcnow().replace(microsecond=0)
        dt = datetime.fromisoformat(time_str.replace('+00:00', ''))
        return dt.replace(microsecond=0)
    except ValueError:
        return datetime.utcnow().replace(microsecond=0)


def get_public_ip():
    try:
        return requests.get('https://ifconfig.me/ip', timeout=5).text.strip()
    except: return None

def query_server(server_addr):
    try:
        ip, query_port = server_addr.split(':')
        addr = (ip, int(query_port))
        query_port = int(query_port)
    except: return None
    
    game_port = None # Will try to discover real port
    
    results = {
        "addr": server_addr, 
        "name": None, 
        "map": "", 
        "player_list": [],
        "header_count": 0,
        "query_port": query_port,
        "game_port": game_port
    }
    
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(TIMEOUT)
        try:
            # 1. A2S_INFO
            sock.sendto(A2S_INFO, addr)
            resp = sock.recv(4096)
            
            if resp.startswith(b'\xff\xff\xff\xff\x41'):
                sock.sendto(A2S_INFO + resp[5:], addr)
                resp = sock.recv(4096)
                
            if resp.startswith(b'\xff\xff\xff\xff\x49'): 
                name, pos = read_string(resp, 6)
                map_name, pos = read_string(resp, pos)
                folder, pos = read_string(resp, pos)
                game, pos = read_string(resp, pos)
                
                pos += 2 # Skip ID
                if pos < len(resp):
                    results["header_count"] = resp[pos]

                pos += 1 
                if pos < len(resp):
                    edf = resp[pos]
                    pos += 1
                    if edf & 0x80:
                        if pos + 2 <= len(resp):
                            game_port = struct.unpack('<H', resp[pos:pos+2])[0]
                            results["game_port"] = game_port
                
                results["name"] = name
                results["map"] = map_name
            else: return None

            # 2. A2S_PLAYERS
            sock.sendto(A2S_PLAYER_CHALLENGE, addr)
            resp = sock.recv(4096)
            if resp.startswith(b'\xff\xff\xff\xff\x41'):
                sock.sendto(A2S_PLAYER_HEADER + resp[5:], addr)
                resp = sock.recv(4096)
            
            if resp.startswith(b'\xff\xff\xff\xff\x44'):
                num = resp[5]
                pos = 6
                slot = 0
                for _ in range(num):
                    if pos >= len(resp): break
                    pos += 1 # Skip Index
                    
                    p_name, pos = read_string(resp, pos)
                    
                    if pos + 8 > len(resp): break
                    score, dur = struct.unpack('<if', resp[pos:pos+8])
                    pos += 8
                    
                    # Handle Ghost Players
                    clean = p_name.strip() if p_name else ""
                    if not clean:
                        clean = f"[UNNAMED:{ip}:{query_port}:{slot}]"
                    
                    results["player_list"].append({"name":clean,"score":score,"dur":int(round(dur))})
                    slot += 1
        except: pass
        
    return results if results["name"] else None
    
def _kv_get(conn, key):
    row = conn.execute("SELECT value FROM meta_kv WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None

def _kv_set(conn, key, value):
    conn.execute("""
        INSERT INTO meta_kv (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
    """, (key, value))
    
def is_server_frozen(conn, server_id, scan_time, current_player_count, current_map_id):
    """
    Determines if a server is frozen OR should remain frozen.
    
    Logic:
    1. ALGORITHM: Checks if 'Established' players are stalled (New Player Dilution Fix).
    2. LATCH: If it was ALREADY frozen, it stays frozen until the map changes or it empties.
    """

    # 1. Immediate Pass: If empty, it cannot be frozen (or has implicitly recovered)
    if current_player_count <= 0:
        return False

    # 2. Get Metrics + Previous State in one query
    row = conn.execute("""
        WITH metrics AS (
            SELECT 
                -- Count players connected > 15 mins
                SUM(CASE WHEN (strftime('%s', ?) - strftime('%s', first_seen)) > 900 THEN 1 ELSE 0 END) as established_count,
                
                -- Count established players whose score hasn't changed in > 15 mins
                SUM(CASE 
                    WHEN (strftime('%s', ?) - strftime('%s', first_seen)) > 900 
                    AND (strftime('%s', ?) - strftime('%s', last_score_change)) > 900 
                    THEN 1 ELSE 0 
                END) as stalled_established_count
            FROM fact_active
            WHERE server_id = ?
        )
        SELECT
            -- [0] Algorithm Verdict
            CASE
                WHEN (strftime('%s', ?) - strftime('%s', s.map_start)) > 1800 -- Map > 30m
                 AND m.established_count > 0                                  -- Has Vets
                 AND m.stalled_established_count = m.established_count        -- Vets are stuck
                THEN 1
                ELSE 0
            END as algo_frozen,
            
            -- [1] Latch State
            s.ingest_disabled,
            
            -- [2] Stored Map ID
            s.current_map_id
            
        FROM dim_servers s
        JOIN metrics m ON 1=1
        WHERE s.id = ?
    """, (
        scan_time, scan_time, scan_time, server_id, # CTE params
        scan_time, server_id                        # Select params
    )).fetchone()

    if not row: return False

    algo_says_frozen = bool(row[0])
    was_frozen = bool(row[1])
    db_map_id = row[2]

    # --- DECISION LOGIC ---

    # A. If the algorithm detects a freeze right now, it is frozen.
    if algo_says_frozen:
        return True

    # B. The Safety Latch
    # If the algo says "Healthy" (maybe a new player joined), 
    # BUT we were previously frozen... check if we truly recovered.
    if was_frozen:
        # If the map hasn't changed, the server physically hasn't reset.
        # We ignore the algo's false hope and keep the lock engaged.
        if current_map_id == db_map_id:
            return True 
    
    # C. Otherwise (Algo says healthy AND (Not previously frozen OR Map Rotated))
    return False



def backfill_rollups(conn):
    """
    One-time backfill over all history.
    This can take a while depending on fact_history size, but you only do it once.
    """
    done = _kv_get(conn, "rollups_backfilled")
    if done == "1":
        return

    # Operator daily (factions)
    conn.execute("DELETE FROM fact_operator_daily")
    conn.execute("""
        INSERT INTO fact_operator_daily (day, operator_name, server_count, unique_players, total_playtime_seconds, last_contact)
        SELECT
            date(h.session_start) AS day,
            s.operator_name,
            COUNT(DISTINCT h.server_id) AS server_count,
            COUNT(DISTINCT h.player_id) AS unique_players,
            COALESCE(SUM(h.calculated_duration), 0) AS total_playtime_seconds,
            MAX(h.session_end) AS last_contact
        FROM fact_history h
        JOIN dim_servers s ON h.server_id = s.id
        WHERE s.operator_name IS NOT NULL
          AND s.operator_name != 'Unknown'
        GROUP BY day, s.operator_name
    """)

    # Map daily (stats) from fact_server_history
    conn.execute("DELETE FROM fact_map_daily")
    conn.execute("""
        INSERT INTO fact_map_daily (day, map_id, session_count, total_seconds)
        SELECT
            date(f.session_start) AS day,
            f.map_id,
            COUNT(f.id) AS session_count,
            COALESCE(SUM(f.calculated_duration), 0) AS total_seconds
        FROM fact_server_history f
        WHERE f.map_id IS NOT NULL
        GROUP BY day, f.map_id
    """)

    # Server daily (stats) from fact_history
    conn.execute("DELETE FROM fact_server_daily")
    conn.execute("""
        INSERT INTO fact_server_daily (day, server_id, session_count, total_seconds)
        SELECT
            date(h.session_start) AS day,
            h.server_id,
            COUNT(h.id) AS session_count,
            COALESCE(SUM(h.calculated_duration), 0) AS total_seconds
        FROM fact_history h
        WHERE h.server_id IS NOT NULL
        GROUP BY day, h.server_id
    """)

    # Player daily (stats) from fact_history
    conn.execute("DELETE FROM fact_player_daily")
    conn.execute("""
        INSERT INTO fact_player_daily (day, player_id, session_count, total_seconds)
        SELECT
            date(h.session_start) AS day,
            h.player_id,
            COUNT(h.id) AS session_count,
            COALESCE(SUM(h.calculated_duration), 0) AS total_seconds
        FROM fact_history h
        WHERE h.player_id IS NOT NULL
        GROUP BY day, h.player_id
    """)

    # Daily traffic (unique players/day)
    conn.execute("DELETE FROM fact_traffic_daily")
    conn.execute("""
        INSERT INTO fact_traffic_daily (day, unique_players)
        SELECT
            date(h.session_start) AS day,
            COUNT(DISTINCT h.player_id) AS unique_players
        FROM fact_history h
        WHERE h.player_id IS NOT NULL
        GROUP BY day
    """)
    # --- Operator player daily (for fast unique counts) ---
    conn.execute("DELETE FROM fact_operator_player_daily")
    conn.execute("""
        INSERT INTO fact_operator_player_daily (day, operator_name, player_id)
        SELECT DISTINCT
            date(h.session_start) AS day,
            s.operator_name,
            h.player_id
        FROM fact_history h
        JOIN dim_servers s ON h.server_id = s.id
        WHERE s.operator_name IS NOT NULL
          AND s.operator_name != 'Unknown'
          AND h.player_id IS NOT NULL
    """)
    _kv_set(conn, "rollups_backfilled", "1")

def refresh_recent_rollups(conn, scan_time, days_back=1):
    """
    Recompute rollups for today and the previous day (default),
    because new history rows only arrive for recent timestamps.
    """
    # 1. DEBOUNCE CHECK
    # We store the last run time in meta_kv to avoid running this heavy calc every 3 minutes.
    last_run_str = _kv_get(conn, "last_rollup_time")
    if last_run_str:
        last_run = datetime.strptime(last_run_str, '%Y-%m-%d %H:%M:%S')
        # If it hasn't been 30 minutes yet, skip the rollup
        if (scan_time - last_run).total_seconds() < 1800: # 1800 seconds = 30 mins
            return

    print("[*] Performing Scheduled Stat Rollups (Debounce Cleared)...")
	
    # We refresh for [today - days_back, today]
    # Example days_back=1 -> yesterday + today
    start_day = (scan_time - timedelta(days=days_back)).strftime("%Y-%m-%d")
    end_day = scan_time.strftime("%Y-%m-%d")

    # Operator daily
    conn.execute("DELETE FROM fact_operator_daily WHERE day BETWEEN ? AND ?", (start_day, end_day))
    conn.execute("""
        INSERT INTO fact_operator_daily (day, operator_name, server_count, unique_players, total_playtime_seconds, last_contact)
        SELECT
            date(h.session_start) AS day,
            s.operator_name,
            COUNT(DISTINCT h.server_id) AS server_count,
            COUNT(DISTINCT h.player_id) AS unique_players,
            COALESCE(SUM(h.calculated_duration), 0) AS total_playtime_seconds,
            MAX(h.session_end) AS last_contact
        FROM fact_history h
        JOIN dim_servers s ON h.server_id = s.id
        WHERE date(h.session_start) BETWEEN ? AND ?
          AND s.operator_name IS NOT NULL
          AND s.operator_name != 'Unknown'
        GROUP BY day, s.operator_name
    """, (start_day, end_day))

    # Map daily
    conn.execute("DELETE FROM fact_map_daily WHERE day BETWEEN ? AND ?", (start_day, end_day))
    conn.execute("""
        INSERT INTO fact_map_daily (day, map_id, session_count, total_seconds)
        SELECT
            date(f.session_start) AS day,
            f.map_id,
            COUNT(f.id) AS session_count,
            COALESCE(SUM(f.calculated_duration), 0) AS total_seconds
        FROM fact_server_history f
        WHERE date(f.session_start) BETWEEN ? AND ?
          AND f.map_id IS NOT NULL
        GROUP BY day, f.map_id
    """, (start_day, end_day))

    # Server daily
    conn.execute("DELETE FROM fact_server_daily WHERE day BETWEEN ? AND ?", (start_day, end_day))
    conn.execute("""
        INSERT INTO fact_server_daily (day, server_id, session_count, total_seconds)
        SELECT
            date(h.session_start) AS day,
            h.server_id,
            COUNT(h.id) AS session_count,
            COALESCE(SUM(h.calculated_duration), 0) AS total_seconds
        FROM fact_history h
        WHERE date(h.session_start) BETWEEN ? AND ?
          AND h.server_id IS NOT NULL
        GROUP BY day, h.server_id
    """, (start_day, end_day))

    # Player daily
    conn.execute("DELETE FROM fact_player_daily WHERE day BETWEEN ? AND ?", (start_day, end_day))
    conn.execute("""
        INSERT INTO fact_player_daily (day, player_id, session_count, total_seconds)
        SELECT
            date(h.session_start) AS day,
            h.player_id,
            COUNT(h.id) AS session_count,
            COALESCE(SUM(h.calculated_duration), 0) AS total_seconds
        FROM fact_history h
        WHERE date(h.session_start) BETWEEN ? AND ?
          AND h.player_id IS NOT NULL
        GROUP BY day, h.player_id
    """, (start_day, end_day))

    # Daily traffic
    conn.execute("DELETE FROM fact_traffic_daily WHERE day BETWEEN ? AND ?", (start_day, end_day))
    conn.execute("""
        INSERT INTO fact_traffic_daily (day, unique_players)
        SELECT
            date(h.session_start) AS day,
            COUNT(DISTINCT h.player_id) AS unique_players
        FROM fact_history h
        WHERE date(h.session_start) BETWEEN ? AND ?
          AND h.player_id IS NOT NULL
        GROUP BY day
    """, (start_day, end_day))
    # --- Operator player daily ---
    conn.execute("""
        DELETE FROM fact_operator_player_daily
        WHERE day BETWEEN ? AND ?
    """, (start_day, end_day))

    conn.execute("""
        INSERT INTO fact_operator_player_daily (day, operator_name, player_id)
        SELECT DISTINCT
            date(h.session_start),
            s.operator_name,
            h.player_id
        FROM fact_history h
        JOIN dim_servers s ON h.server_id = s.id
        WHERE date(h.session_start) BETWEEN ? AND ?
          AND s.operator_name IS NOT NULL
          AND s.operator_name != 'Unknown'
          AND h.player_id IS NOT NULL
    """, (start_day, end_day))
    _kv_set(conn, "last_rollup_time", scan_time.strftime('%Y-%m-%d %H:%M:%S'))

def get_map_id(conn, map_cache, m_name):
    if m_name in map_cache:
        return map_cache[m_name]

    cur = conn.execute(
        "INSERT OR IGNORE INTO dim_maps (name) VALUES (?)",
        (m_name,)
    )

    mid = (
        cur.lastrowid
        or conn.execute(
            "SELECT id FROM dim_maps WHERE name = ?",
            (m_name,)
        ).fetchone()[0]
    )

    map_cache[m_name] = mid
    return mid


def get_player_id(conn, player_cache, p_name):
    # Fast path: already cached
    if p_name in player_cache:
        return player_cache[p_name]

    # Insert or fetch by name (authoritative)
    conn.execute("""
        INSERT OR IGNORE INTO dim_players (name, real_name)
        VALUES (?, ?)
    """, (p_name, p_name))

    pid = conn.execute(
        "SELECT id FROM dim_players WHERE name = ?",
        (p_name,)
    ).fetchone()[0]

    player_cache[p_name] = pid
    return pid

def get_server_targets():
    """
    Attempts to fetch the server list from the Steam Master Server.
    If the API times out or fails, it falls back to the existing list 
    of servers stored in the local database.
    """
    targets = []
    
    # --- STRATEGY 1: STEAM API ---
    try:
        print("[*] Contacting Steam Master Server...")
        r = requests.get(API_URL, timeout=10) # 10s timeout
        
        if r.status_code == 200:
            data = r.json()
            servers = data.get("response", {}).get("servers", [])
            targets = [s['addr'] for s in servers]
            print(f"[*] Steam API: Acquired {len(targets)} targets.")
            return targets
        else:
            print(f"[!] Steam API Error: Received Status Code {r.status_code}")
            
    except requests.exceptions.RequestException as e:
        print(f"[!] Steam API Connection Failed: {e}")
    except Exception as e:
        print(f"[!] Steam API Parse Error: {e}")

    # --- STRATEGY 2: DATABASE FALLBACK ---
    # We only reach here if Strategy 1 failed or returned 0 servers
    print("[*] Activating Local Database Fallback Protocol...")
    
    try:
        with sqlite3.connect(DB_FILE) as conn:
            # Fetch every server we have ever seen
            rows = conn.execute("SELECT ip_address, query_port FROM dim_servers").fetchall()
            
            # Reconstruct the "IP:PORT" string format expected by the scanner
            targets = [f"{row[0]}:{row[1]}" for row in rows]
            
            print(f"[*] Local DB: Recovered {len(targets)} known targets from history.")
            
    except Exception as e:
        print(f"[!] CRITICAL FAILURE: Could not read local DB for fallback: {e}")

    return targets

def main():
    start_time = time.time()
    scan_time = datetime.utcnow().replace(microsecond=0)
    print(f"--- [ SCAN STARTED: {scan_time.strftime('%H:%M:%S')} ] ---")
    
    init_db()

    public_ip = get_public_ip()
    if public_ip:
        print(f"[*] Identity Confirmed: {public_ip}")

    # --- NEW: Get Targets with Fallback ---
    addrs = get_server_targets()
    
    if not addrs:
        print("[!] No targets acquired from API or DB. Aborting scan.")
        return

    valid_results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {}
        for public_addr in addrs:
            target_ip_port = public_addr
            if public_ip and public_addr.startswith(public_ip):
                try:
                    _, port = public_addr.split(':')
                    target_ip_port = f"{LOCAL_LOOPBACK_IP}:{port}"
                except: pass

            future = executor.submit(query_server, target_ip_port)
            futures[future] = public_addr

        for f in concurrent.futures.as_completed(futures):
            original_public_addr = futures[f] # <--- This is the Real Public IP
            try:
                res = f.result()
                if res:
                    # FIX: The query used 127.0.0.1, but the Database needs the Public IP.
                    # We overwrite the 'addr' field in the result with the original target.
                    res['addr'] = original_public_addr 
                    
                    valid_results.append(res)
            except: pass
    
    print(f"[*] Processing {len(valid_results)} responses...")

    # --- CALC TOTALS ---
    total_active_servers = len(valid_results)
    total_active_players = sum(s["header_count"] for s in valid_results)
    # -------------------

    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        
        map_cache = {row[1]: row[0] for row in conn.execute("SELECT id, name FROM dim_maps").fetchall()}
        player_cache = {
            row[1]: row[0]
            for row in conn.execute(
                "SELECT id, name FROM dim_players"
            ).fetchall()
        }

        
        server_cache = {}
        # New Cache Key Format: "IP:QueryPort"
        # FIX: Loop is correctly indented
        for row in conn.execute("SELECT id, ip_address, query_port, game_port, current_map_id, map_start, player_count, last_seen, name, current_session_uuid, operator_name FROM dim_servers").fetchall():
            cache_key = f"{row[1]}:{row[2]}" # IP:QueryPort
            server_cache[cache_key] = {
                'id': row[0], 
                'game_port': row[3],
                'map_id': row[4], 
                'map_start': parse_iso_time(row[5]), 
                'count': row[6], 
                'last_seen': row[7],
                'name': row[8],
                'session_uuid': row[9],
                'operator_name': row[10]
            }


        prune_limit = (scan_time - timedelta(minutes=PRUNE_THRESHOLD)).strftime('%Y-%m-%d %H:%M:%S')
        
        # --- [PRUNING UPDATE] Transfer calculated_duration from fact_active to fact_history ---
        conn.execute("""
            INSERT INTO fact_history (server_id, player_id, map_id, final_score, total_time, session_start, session_end, session_uuid, calculated_duration)
            SELECT server_id, player_id, map_id, score, duration, first_seen, last_seen, session_uuid, calculated_duration
            FROM fact_active
            WHERE last_seen < ?
        """, (prune_limit,))
        
        conn.execute("DELETE FROM fact_active WHERE last_seen < ?", (prune_limit,))
        # --- ZOMBIE / SCORE-STAGNATION PRUNE ---
        ZOMBIE_HOURS = 2
        zombie_limit = (scan_time - timedelta(hours=ZOMBIE_HOURS)).strftime('%Y-%m-%d %H:%M:%S')

        conn.execute("""
            INSERT INTO fact_history (
                server_id,
                player_id,
                map_id,
                final_score,
                total_time,
                session_start,
                session_end,
                session_uuid,
                calculated_duration
            )
            SELECT
                server_id,
                player_id,
                map_id,
                score,
                duration,
                first_seen,
                last_seen,
                session_uuid,
                calculated_duration
            FROM fact_active
            WHERE last_score_change IS NOT NULL
              AND last_score_change < ?
        """, (zombie_limit,))

        conn.execute("""
            DELETE FROM fact_active
            WHERE last_score_change IS NOT NULL
              AND last_score_change < ?
        """, (zombie_limit,))
        
        for s in valid_results:
            current_ip = s["addr"].split(':')[0]
            current_qport = s["query_port"]
            cache_key = f"{current_ip}:{current_qport}"
            
            map_id = get_map_id(conn, map_cache, s["map"])

            
            # --- CALCULATE OPERATOR ---
            operator_name = clean_server_name(s["name"], current_ip)

            # --- GET LOCATION FROM DB ---
            # Use the helper to resolve against ip_ranges
            location_val = resolve_geo_db(conn, current_ip)
            
            # --- ATOMIC UPSERT ---
            # 1. Ensure server record exists (Using Identity: IP + QueryPort)
            # Added operator_name and location to INSERT statement
            conn.execute("""
                INSERT OR IGNORE INTO dim_servers (ip_address, query_port, game_port, name, current_map_id, last_seen, map_start, current_session_uuid, operator_name, location) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (current_ip, current_qport, s["game_port"], s["name"], map_id, scan_time, scan_time, str(uuid.uuid4()), operator_name, location_val))
            
            # 2. Retrieve authoritative ID from DB (or Cache if confident)
            if cache_key in server_cache:
                sdata = server_cache[cache_key]
                sid = sdata['id']
                db_game_port = sdata['game_port']
                db_map_id = sdata['map_id']
                db_map_start = sdata['map_start']
                db_session_uuid = sdata['session_uuid']
            else:
                # Cache miss (New IP or Cold Start). 
                # 1. Try DB lookup by IP
                row = conn.execute("SELECT id, game_port, current_session_uuid, current_map_id, map_start FROM dim_servers WHERE ip_address=? AND query_port=?", (current_ip, current_qport)).fetchone()
                
                if row:
                    sid = row[0]
                    db_game_port = row[1]
                    db_session_uuid = row[2]
                    db_map_id = row[3]
                    db_map_start = parse_iso_time(row[4])
                else:
                    # 2. Try DB lookup by Exact Name (Dynamic IP Recovery)
                    
                    # --- GENERIC NAME BLACKLIST ---
                    # These names are too common. Never merge them.
                    GENERIC_NAMES = {
                        "killing floor 2 server", "kf2 server", "kf2", "killing floor 2", 
                        "server", "dedicated server", "public server", "survival", 
                        "endless", "hard", "suicidal", "hoe", "hell on earth",
                        "gameservers.com", "linuxgsm", "nitrado.net", 
                        "kf2 server endless", "kf2 server hard and long",
                        "kf2 server long and hard", "kf2 server the zone",
                        "kf2 server very hard and long", "mgga make gaming great again"
                    }
                    
                    is_generic = s["name"].lower().strip() in GENERIC_NAMES
                    
                    # Also blacklist purely numeric names or very short names
                    if len(s["name"]) < 4 or s["name"].isdigit():
                        is_generic = True
                    # ------------------------------

                    candidates = []
                    if not is_generic:
                        candidates = conn.execute("SELECT id, game_port, current_session_uuid, current_map_id, map_start, ip_address FROM dim_servers WHERE name=?", (s["name"],)).fetchall()
                    
                    if len(candidates) >= 1:
                        # Found exactly one match. Assume it moved.
                        row = candidates[0]
                        sid = row[0]
                        db_game_port = row[1]
                        db_session_uuid = row[2]
                        db_map_id = row[3]
                        db_map_start = parse_iso_time(row[4])
                        old_ip = row[5]
                        
                        print(f"[!] Dynamic IP: {s['name']} moved from {old_ip} to {current_ip}")
                        try:
                            # Migrate record to new IP
                            conn.execute("UPDATE dim_servers SET ip_address=?, query_port=? WHERE id=?", (current_ip, current_qport, sid))
                        except sqlite3.IntegrityError:
                            # Collision (Rare): Just make a new ID
                            sid = None
                    else:
                        sid = None

                if sid is None:
                    # 3. Truly New Server. Create ID.
                    conn.execute("""
                        INSERT OR IGNORE INTO dim_servers (ip_address, query_port, game_port, name, current_map_id, last_seen, map_start, current_session_uuid, operator_name, location) 
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (current_ip, current_qport, s["game_port"], s["name"], map_id, scan_time, scan_time, str(uuid.uuid4()), operator_name, location_val))
                    
                    # Fetch ID again
                    sid = conn.execute("SELECT id FROM dim_servers WHERE ip_address=? AND query_port=?", (current_ip, current_qport)).fetchone()[0]
                    
                    # Default values for new server
                    db_game_port = s["game_port"]
                    db_map_id = map_id
                    db_map_start = scan_time
                    db_session_uuid = str(uuid.uuid4())

            # 3. Resolve Dynamic Data
            final_game_port = s["game_port"] if s["game_port"] else db_game_port
            
            # Logic: Determine if we need a NEW session UUID
            current_session_uuid = db_session_uuid
            
            # 3.5 --- FREEZE DETECTION ---
            # Pass 'map_id' (from current scan) to the function
            frozen_now = is_server_frozen(conn, sid, scan_time, s["header_count"], map_id)
            
            if frozen_now:
                # CONFIRMED FROZEN (Algorithm detected it OR Latch is holding it)
                conn.execute("""
                    UPDATE dim_servers
                    SET frozen_since = COALESCE(frozen_since, ?),
                        ingest_disabled = 1
                    WHERE id = ?
                """, (scan_time, sid))
            
            else:
                # CONFIRMED HEALTHY
                # Check if we need to run a "Recovery" routine (clean up the mess)
                row = conn.execute(
                    "SELECT ingest_disabled FROM dim_servers WHERE id = ?", (sid,)
                ).fetchone()

                if row and row[0]:
                    print(f"[*] Server {s['name']} recovered! Unfreezing.")
                    # Server recovered -> force clean session
                    current_session_uuid = str(uuid.uuid4())
                    db_map_start = scan_time

                    conn.execute("""
                        UPDATE dim_servers
                        SET frozen_since = NULL,
                            ingest_disabled = 0,
                            current_session_uuid = ?,
                            map_start = ?
                        WHERE id = ?
                    """, (current_session_uuid, scan_time, sid))
            # 4. Map Rotation History
            if not frozen_now:
                if map_id != db_map_id:
                    # --- UPDATE: Calculate duration in Python for server history ---
                    duration_sec = int((scan_time - db_map_start).total_seconds())
                    
                    conn.execute("""
                        INSERT INTO fact_server_history (server_id, map_id, session_start, session_end, reason, session_uuid, calculated_duration)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (sid, db_map_id, db_map_start, scan_time, "Map Rotation", db_session_uuid, duration_sec))
                    
                    db_map_start = scan_time
                    current_session_uuid = str(uuid.uuid4()) # New Match = New ID
                    
                # --- [NEW] SECTION 4.5: MATCH RESTART DETECTION ---
                # Logic: If map is the same, but scores dropped from "High" to "Near Zero", it's a wipe.
                elif map_id == db_map_id:
                    # 1. Get the aggregate score from the PREVIOUS scan (DB State)
                    # We need to know what the score was before we overwrite it.
                    row = conn.execute("SELECT SUM(score) FROM fact_active WHERE server_id=?", (sid,)).fetchone()
                    prev_total_score = row[0] if row and row[0] else 0
                    
                    # 2. Calculate the aggregate score from the CURRENT scan (Live State)
                    curr_total_score = sum(p['score'] for p in s['player_list'])

                    # 3. The "Wipe" Thresholds
                    # prev_total > 500: Ensures we don't log restarts for empty/idle servers.
                    # curr_total < 200: Allows for starting cash/points, but implies a hard reset.
                    if prev_total_score > 500 and curr_total_score < 200:
                        # --- UPDATE: Calculate duration in Python for server history ---
                        duration_sec = int((scan_time - db_map_start).total_seconds())
                        
                        conn.execute("""
                            INSERT INTO fact_server_history (server_id, map_id, session_start, session_end, reason, session_uuid, calculated_duration)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (sid, db_map_id, db_map_start, scan_time, "Match Restart", db_session_uuid, duration_sec))
                        
                        # CRITICAL: Reset the timer. 
                        # If we don't do this, the next "session" will look like it lasted 4 hours 
                        # instead of the 20 minutes it actually took them to fail.
                        db_map_start = scan_time
                        current_session_uuid = str(uuid.uuid4()) # Restart = New ID
                # --------------------------------------------------
                
            # SECTION 4.6
            # 🔥 PLAYER SESSION FINALIZATION 🔥
            if not frozen_now and current_session_uuid != db_session_uuid:
                conn.execute("""
                    INSERT INTO fact_history (
                        server_id,
                        player_id,
                        map_id,
                        final_score,
                        total_time,
                        session_start,
                        session_end,
                        session_uuid,
                        calculated_duration
                    )
                    SELECT
                        server_id,
                        player_id,
                        map_id,
                        score,
                        duration,
                        first_seen,
                        last_seen,
                        session_uuid,
                        calculated_duration
                    FROM fact_active
                    WHERE server_id=? AND session_uuid=?
                """, (sid, db_session_uuid))
            # 2. Remove them from active so they can be re-added cleanly
                conn.execute("""
                    DELETE FROM fact_active
                    WHERE server_id=? AND session_uuid=?
                """, (sid, db_session_uuid))     
                
            # 5. Update Server State
            # Added operator_name=? and location=? to SET clause
            conn.execute("""
                UPDATE dim_servers 
                SET name=?, current_map_id=?, player_count=?, map_start=?, last_seen=?, game_port=?, current_session_uuid=?, operator_name=?, location=?
                WHERE id=?
            """, (s["name"], map_id, s["header_count"], db_map_start, scan_time, final_game_port, current_session_uuid, operator_name, location_val, sid)) 
            
            if not current_session_uuid:
                current_session_uuid = str(uuid.uuid4())
            conn.execute("""
                UPDATE dim_servers
                SET current_session_uuid = ?
                WHERE id = ?
            """, (current_session_uuid, sid))    
            # 6. Update Sessions (with score-stagnation tracking)
            if not frozen_now:
                for p in s["player_list"]:
                    pid = get_player_id(conn, player_cache, p["name"])
                    now_ts = scan_time
                    conn.execute("""
                        INSERT INTO fact_active (
                            server_id,
                            player_id,
                            map_id,
                            score,
                            duration,
                            calculated_duration,
                            first_seen,
                            last_seen,
                            session_uuid,
                            last_score_change
                        )
                        VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                        ON CONFLICT(server_id, player_id) DO UPDATE SET
                            -- Initialize if NULL, update if score changes, otherwise preserve
                            last_score_change = CASE
                                WHEN fact_active.last_score_change IS NULL
                                    THEN excluded.last_seen
                                WHEN fact_active.score != excluded.score
                                    THEN excluded.last_seen
                                ELSE fact_active.last_score_change
                            END,

                            score = excluded.score,
                            duration = excluded.duration,
                            calculated_duration =
                                (strftime('%s', excluded.last_seen) - strftime('%s', fact_active.first_seen)),
                            map_id = excluded.map_id,
                            last_seen = excluded.last_seen,
                            session_uuid = excluded.session_uuid
                    """, (
                        sid,
                        pid,
                        map_id,
                        p["score"],
                        p["dur"],
                        scan_time,
                        scan_time,
                        current_session_uuid,
                        scan_time
                    ))



        conn.execute("""
            INSERT INTO fact_global_stats (scan_time, active_servers, active_players)
            VALUES (?, ?, ?)
        """, (scan_time, total_active_servers, total_active_players))
        # --- DEAD SERVER CLEANUP ---
        # 1. Define the cutoff (15 minutes ago)
        server_timeout = (scan_time - timedelta(minutes=15))
        
        # 2. Move "Missing in Action" servers to history
        # We only archive them if they aren't already marked as empty/processed 
        # (Assuming player_count > 0 acts as our "active" flag here, 
        # otherwise you log a history entry for every 15m cycle a server stays dead)
        # --- UPDATE: Calculate duration using SQL math for dead servers ---
        conn.execute("""
            INSERT INTO fact_server_history (server_id, map_id, session_start, session_end, reason, session_uuid, calculated_duration)
            SELECT id, current_map_id, map_start, last_seen, 'Connection Lost', current_session_uuid,
                   (strftime('%s', last_seen) - strftime('%s', map_start))
            FROM dim_servers
            WHERE last_seen < ? AND player_count > 0
        """, (server_timeout,))

        # 3. Mark them as empty so they stop showing up as active
        # We also reset map_start to prevent duplicate history entries if it stays dead
        conn.execute("""
            UPDATE dim_servers
            SET player_count = 0, map_start = ?
            WHERE last_seen < ? AND player_count > 0
        """, (scan_time, server_timeout))
        # ---------------------------    
        # --- ROLLUPS ---
        backfill_rollups(conn)              # runs once, then becomes a no-op
        refresh_recent_rollups(conn, scan_time, days_back=1)  # yesterday + today
        
        conn.commit()
    
    print(f"--- [ CYCLE COMPLETE: {time.time() - start_time:.2f}s | Players: {total_active_players} ] ---")

if __name__ == "__main__":
    main()