import sqlite3

SCHEMA="""
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS source_file(id INTEGER PRIMARY KEY,command TEXT NOT NULL,sha TEXT NOT NULL,raw BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS event(id TEXT PRIMARY KEY,kind TEXT NOT NULL,sha TEXT NOT NULL,raw BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS point(id TEXT PRIMARY KEY,zone TEXT NOT NULL,category TEXT NOT NULL,inspector_org TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS batch(id TEXT PRIMARY KEY,point_id TEXT NOT NULL,category TEXT NOT NULL,bag_count INTEGER NOT NULL,declared_kg REAL NOT NULL,at TEXT NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sample(id TEXT PRIMARY KEY,batch_id TEXT NOT NULL,inspector TEXT NOT NULL,at TEXT NOT NULL,contamination REAL NOT NULL,bag_refs TEXT NOT NULL,revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS batch_decision(id INTEGER PRIMARY KEY,batch_id TEXT NOT NULL,state TEXT NOT NULL,usable_kg REAL NOT NULL,reason TEXT NOT NULL,source_id TEXT);
CREATE TABLE IF NOT EXISTS batch_investigation(id INTEGER PRIMARY KEY,batch_id TEXT NOT NULL,weigh_id TEXT NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS vehicle(id TEXT PRIMARY KEY,category TEXT NOT NULL,capacity_kg REAL NOT NULL,zone TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS route(id TEXT PRIMARY KEY,vehicle_id TEXT NOT NULL,starts TEXT NOT NULL,ends TEXT NOT NULL,zone TEXT NOT NULL,dispatcher TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS load(id TEXT PRIMARY KEY,batch_id TEXT NOT NULL,route_id TEXT NOT NULL,loaded_at TEXT NOT NULL,kg REAL NOT NULL,seal TEXT NOT NULL,driver TEXT NOT NULL,point_witness TEXT NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS station(id TEXT PRIMARY KEY,zone TEXT NOT NULL,category TEXT NOT NULL,tolerance_pct REAL NOT NULL);
CREATE TABLE IF NOT EXISTS weigh(id TEXT PRIMARY KEY,station_id TEXT NOT NULL,route_id TEXT NOT NULL,seal TEXT NOT NULL,at TEXT NOT NULL,gross REAL NOT NULL,tare REAL NOT NULL,operator TEXT NOT NULL,reweigh_of TEXT,state TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS handoff(id INTEGER PRIMARY KEY,weigh_id TEXT NOT NULL,load_id TEXT NOT NULL,net_kg REAL NOT NULL,delta_kg REAL NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS performance(id INTEGER PRIMARY KEY,batch_id TEXT NOT NULL,credited_kg REAL NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decision_log(id INTEGER PRIMARY KEY,system TEXT NOT NULL,object_id TEXT NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL,event_id TEXT,at TEXT DEFAULT CURRENT_TIMESTAMP);

-- Offline unloading evidence.  Raw rows are append-only: a re-scan or a
-- witness supplement is another row, never an edit of an earlier reading.
CREATE TABLE IF NOT EXISTS contract(id TEXT PRIMARY KEY,contract_no TEXT NOT NULL,import_no TEXT,plan TEXT NOT NULL,station_id TEXT,route_id TEXT NOT NULL,seal TEXT NOT NULL,carrier TEXT,importer TEXT,signed_at TEXT NOT NULL,selected INTEGER NOT NULL DEFAULT 0,state TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS contract_selection(id TEXT PRIMARY KEY,contract_no TEXT NOT NULL,route_id TEXT,seal TEXT,at TEXT NOT NULL,witness TEXT,state TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS calibration(id TEXT PRIMARY KEY,station_id TEXT NOT NULL,shift TEXT NOT NULL,zero_min REAL NOT NULL,zero_max REAL NOT NULL,ticket TEXT,at TEXT NOT NULL,witness TEXT,supersedes TEXT,state TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS seal_manifest(id TEXT PRIMARY KEY,route_id TEXT NOT NULL,seal TEXT NOT NULL,batch_ids TEXT NOT NULL,at TEXT NOT NULL,capture_sha TEXT,state TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS unload_session(id TEXT PRIMARY KEY,station_id TEXT NOT NULL,route_id TEXT NOT NULL,seal TEXT NOT NULL,shift TEXT NOT NULL,at TEXT NOT NULL,witness TEXT NOT NULL,contract_no TEXT,expected_containers INTEGER,closed INTEGER NOT NULL DEFAULT 1,capture_sha TEXT NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS unload_container(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,sequence INTEGER NOT NULL,container_code TEXT NOT NULL,seal_in TEXT,seal_out TEXT,before_kg REAL,after_kg REAL,raw_kg REAL NOT NULL,capture_sha TEXT NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS container_scan(id TEXT PRIMARY KEY,container_id TEXT NOT NULL,batch_id TEXT,at TEXT NOT NULL,witness TEXT,capture_sha TEXT,supersedes TEXT,kind TEXT NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS unload_adjudication(id INTEGER PRIMARY KEY,route_id TEXT NOT NULL,seal TEXT NOT NULL,weigh_id TEXT,station_id TEXT NOT NULL,shift TEXT NOT NULL,rule_version TEXT NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL,z REAL,zero_min REAL,zero_max REAL,raw_sum REAL,net_kg REAL,expected_containers INTEGER,observed_containers INTEGER,evidence TEXT NOT NULL,candidate_map TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS container_candidate(id INTEGER PRIMARY KEY,adjudication_id INTEGER NOT NULL,container_id TEXT NOT NULL,sequence INTEGER NOT NULL,batch_id TEXT,feasible INTEGER NOT NULL,reason TEXT NOT NULL,raw_ticket TEXT);
CREATE TABLE IF NOT EXISTS batch_net_bound(id INTEGER PRIMARY KEY,adjudication_id INTEGER NOT NULL,batch_id TEXT NOT NULL,load_id TEXT,lower_kg REAL,upper_kg REAL,proved_kg REAL,eligible INTEGER NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL,evidence TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS certificate(id INTEGER PRIMARY KEY,adjudication_id INTEGER NOT NULL,contract_no TEXT,plan TEXT NOT NULL,route_id TEXT NOT NULL,seal TEXT NOT NULL,weigh_id TEXT,batch_id TEXT,kg REAL,lower_kg REAL,upper_kg REAL,state TEXT NOT NULL,reason TEXT NOT NULL,rule_version TEXT NOT NULL,raw_tickets TEXT NOT NULL,candidate_map TEXT NOT NULL,bounds TEXT NOT NULL,selected INTEGER NOT NULL DEFAULT 0,active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS performance_evidence(id INTEGER PRIMARY KEY,batch_id TEXT NOT NULL,performance_id INTEGER,basis TEXT NOT NULL,certificate_id INTEGER,rule_version TEXT,evidence TEXT NOT NULL,reason TEXT NOT NULL);
"""

def connect(path):
    db=sqlite3.connect(path);db.row_factory=sqlite3.Row;db.executescript(SCHEMA);return db
def one(db,sql,args=()):return db.execute(sql,args).fetchone()
def many(db,sql,args=()):return db.execute(sql,args).fetchall()
def log(db,system,key,state,reason,event_id=None):
    db.execute('INSERT INTO decision_log(system,object_id,state,reason,event_id) VALUES(?,?,?,?,?)',
               (system,key,state,reason,event_id))
