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
CREATE TABLE IF NOT EXISTS weigh(id TEXT PRIMARY KEY,station_id TEXT NOT NULL,route_id TEXT NOT NULL,seal TEXT NOT NULL,at TEXT NOT NULL,gross REAL NOT NULL,tare REAL NOT NULL,operator TEXT NOT NULL,reweigh_of TEXT,contract_no TEXT,state TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS handoff(id INTEGER PRIMARY KEY,weigh_id TEXT NOT NULL,load_id TEXT NOT NULL,net_kg REAL NOT NULL,delta_kg REAL NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS performance(id INTEGER PRIMARY KEY,batch_id TEXT NOT NULL,credited_kg REAL NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS contract(id TEXT PRIMARY KEY,contract_no TEXT NOT NULL UNIQUE,form TEXT NOT NULL,party TEXT NOT NULL,signed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS shift_calibration(id TEXT PRIMARY KEY,station_id TEXT NOT NULL,shift TEXT NOT NULL,zero_min REAL NOT NULL,zero_max REAL NOT NULL,operator TEXT NOT NULL,witness TEXT NOT NULL,at TEXT NOT NULL,raw_digest TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS unload_segment(id TEXT PRIMARY KEY,station_id TEXT NOT NULL,route_id TEXT NOT NULL,seal TEXT NOT NULL,shift TEXT NOT NULL,seq INTEGER NOT NULL,container_code TEXT,batch_hint TEXT,pre_kg REAL NOT NULL,post_kg REAL NOT NULL,operator TEXT NOT NULL,witness TEXT NOT NULL,at TEXT NOT NULL,raw_digest TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS unload_close(id TEXT PRIMARY KEY,route_id TEXT NOT NULL,seal TEXT NOT NULL,shift TEXT NOT NULL,container_count INTEGER NOT NULL,operator TEXT NOT NULL,witness TEXT NOT NULL,at TEXT NOT NULL,raw_digest TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS container_rescan(id TEXT PRIMARY KEY,segment_id TEXT NOT NULL,container_code TEXT NOT NULL,batch_id TEXT NOT NULL,operator TEXT NOT NULL,witness TEXT NOT NULL,at TEXT NOT NULL,raw_digest TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS adjudication(id INTEGER PRIMARY KEY,route_id TEXT NOT NULL,seal TEXT NOT NULL,version INTEGER NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL,z REAL,vehicle_net REAL,weigh_id TEXT,calibration_id TEXT,close_id TEXT,rules_version TEXT NOT NULL,cause_event TEXT,relations TEXT NOT NULL,fingerprint TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS adjudication_explanation(id INTEGER PRIMARY KEY,adjudication_id INTEGER NOT NULL,idx INTEGER NOT NULL,z REAL NOT NULL,assignment TEXT NOT NULL,totals TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS batch_bound(id INTEGER PRIMARY KEY,adjudication_id INTEGER NOT NULL,batch_id TEXT NOT NULL,min_kg REAL,max_kg REAL,state TEXT NOT NULL,min_tickets TEXT NOT NULL,max_tickets TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS certificate(id INTEGER PRIMARY KEY,adjudication_id INTEGER NOT NULL,form TEXT NOT NULL,batch_id TEXT,kg REAL,state TEXT NOT NULL,reason TEXT NOT NULL,is_primary INTEGER NOT NULL DEFAULT 0,rules_version TEXT NOT NULL,tickets TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decision_log(id INTEGER PRIMARY KEY,system TEXT NOT NULL,object_id TEXT NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL,event_id TEXT,at TEXT DEFAULT CURRENT_TIMESTAMP);
"""

def connect(path):
    db=sqlite3.connect(path);db.row_factory=sqlite3.Row;db.executescript(SCHEMA)
    weigh_cols=[r['name'] for r in db.execute('PRAGMA table_info(weigh)')]
    if 'contract_no' not in weigh_cols:db.execute('ALTER TABLE weigh ADD COLUMN contract_no TEXT')
    return db
def one(db,sql,args=()):return db.execute(sql,args).fetchone()
def many(db,sql,args=()):return db.execute(sql,args).fetchall()
def log(db,system,key,state,reason,event_id=None):
    db.execute('INSERT INTO decision_log(system,object_id,state,reason,event_id) VALUES(?,?,?,?,?)',
               (system,key,state,reason,event_id))
