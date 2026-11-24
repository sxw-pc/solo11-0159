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
CREATE TABLE IF NOT EXISTS baffle_trial(id TEXT PRIMARY KEY,site TEXT NOT NULL,old_rate INTEGER NOT NULL,new_rate INTEGER NOT NULL,observed_delta INTEGER NOT NULL,loose_lower INTEGER NOT NULL,loose_upper INTEGER NOT NULL,modes TEXT NOT NULL,worlds TEXT NOT NULL,status TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS baffle_shift(id TEXT PRIMARY KEY,trial_id TEXT NOT NULL,seq INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS baffle_feed(id TEXT PRIMARY KEY,trial_id TEXT NOT NULL,shift_id TEXT NOT NULL,origin_grade INTEGER,calibrated_offset INTEGER);
CREATE TABLE IF NOT EXISTS baffle_point(id TEXT PRIMARY KEY,trial_id TEXT NOT NULL,role TEXT NOT NULL,baffle_state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS baffle_fixture(id TEXT PRIMARY KEY,trial_id TEXT NOT NULL,feed_id TEXT,state TEXT NOT NULL,reason TEXT NOT NULL,old_fraction INTEGER,new_fraction INTEGER,witness TEXT);
CREATE TABLE IF NOT EXISTS baffle_switch(id TEXT PRIMARY KEY,trial_id TEXT NOT NULL,point_id TEXT NOT NULL,shift_id TEXT NOT NULL,from_state TEXT NOT NULL,to_state TEXT NOT NULL,empty_verified INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS baffle_origin_link(id TEXT PRIMARY KEY,trial_id TEXT NOT NULL,shift_id TEXT NOT NULL,retro_feed_id TEXT NOT NULL,control_feed_id TEXT NOT NULL,delta INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS baffle_reading(id TEXT PRIMARY KEY,trial_id TEXT NOT NULL,shift_id TEXT NOT NULL,feed_id TEXT NOT NULL,fixture_id TEXT,point_id TEXT,baffle_state TEXT NOT NULL,grade INTEGER NOT NULL,provenance TEXT NOT NULL,contrastable INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS baffle_attestation(id TEXT PRIMARY KEY,trial_id TEXT NOT NULL,mode TEXT NOT NULL,old_reading_id TEXT NOT NULL,new_reading_id TEXT NOT NULL,effect_lower INTEGER NOT NULL,effect_upper INTEGER NOT NULL,provenance_key TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decision_log(id INTEGER PRIMARY KEY,system TEXT NOT NULL,object_id TEXT NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL,event_id TEXT,at TEXT DEFAULT CURRENT_TIMESTAMP);
"""

def connect(path):
    db=sqlite3.connect(path);db.row_factory=sqlite3.Row;db.executescript(SCHEMA);return db
def one(db,sql,args=()):return db.execute(sql,args).fetchone()
def many(db,sql,args=()):return db.execute(sql,args).fetchall()
def log(db,system,key,state,reason,event_id=None):
    db.execute('INSERT INTO decision_log(system,object_id,state,reason,event_id) VALUES(?,?,?,?,?)',
               (system,key,state,reason,event_id))
