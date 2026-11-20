import sqlite3

SCHEMA="""
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS source_file(id INTEGER PRIMARY KEY,command TEXT NOT NULL,sha TEXT NOT NULL,raw BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS event(id TEXT PRIMARY KEY,kind TEXT NOT NULL,sha TEXT NOT NULL,raw BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS point(id TEXT PRIMARY KEY,zone TEXT NOT NULL,category TEXT NOT NULL,inspector_org TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS batch(id TEXT PRIMARY KEY,point_id TEXT NOT NULL,category TEXT NOT NULL,bag_count INTEGER NOT NULL,declared_kg REAL NOT NULL,at TEXT NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sample(id TEXT PRIMARY KEY,batch_id TEXT NOT NULL,inspector TEXT NOT NULL,at TEXT NOT NULL,contamination REAL NOT NULL,bag_refs TEXT NOT NULL,revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS batch_decision(id INTEGER PRIMARY KEY,batch_id TEXT NOT NULL,state TEXT NOT NULL,usable_kg REAL NOT NULL,reason TEXT NOT NULL,source_id TEXT);
CREATE TABLE IF NOT EXISTS vehicle(id TEXT PRIMARY KEY,category TEXT NOT NULL,capacity_kg REAL NOT NULL,zone TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS route(id TEXT PRIMARY KEY,vehicle_id TEXT NOT NULL,starts TEXT NOT NULL,ends TEXT NOT NULL,zone TEXT NOT NULL,dispatcher TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS load(id TEXT PRIMARY KEY,batch_id TEXT NOT NULL,route_id TEXT NOT NULL,loaded_at TEXT NOT NULL,kg REAL NOT NULL,seal TEXT NOT NULL,driver TEXT NOT NULL,point_witness TEXT NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS station(id TEXT PRIMARY KEY,zone TEXT NOT NULL,category TEXT NOT NULL,tolerance_pct REAL NOT NULL);
CREATE TABLE IF NOT EXISTS weigh(id TEXT PRIMARY KEY,station_id TEXT NOT NULL,route_id TEXT NOT NULL,seal TEXT NOT NULL,at TEXT NOT NULL,gross REAL NOT NULL,tare REAL NOT NULL,operator TEXT NOT NULL,reweigh_of TEXT,state TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS handoff(id INTEGER PRIMARY KEY,weigh_id TEXT NOT NULL,load_id TEXT NOT NULL,net_kg REAL NOT NULL,delta_kg REAL NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS performance(id INTEGER PRIMARY KEY,batch_id TEXT NOT NULL,credited_kg REAL NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decision_log(id INTEGER PRIMARY KEY,system TEXT NOT NULL,object_id TEXT NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL,event_id TEXT,at TEXT DEFAULT CURRENT_TIMESTAMP);
"""

def connect(path):
    db=sqlite3.connect(path);db.row_factory=sqlite3.Row;db.executescript(SCHEMA);return db
def one(db,sql,args=()):return db.execute(sql,args).fetchone()
def many(db,sql,args=()):return db.execute(sql,args).fetchall()
def log(db,system,key,state,reason,event_id=None):
    db.execute('INSERT INTO decision_log(system,object_id,state,reason,event_id) VALUES(?,?,?,?,?)',
               (system,key,state,reason,event_id))
