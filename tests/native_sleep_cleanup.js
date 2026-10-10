// Execute the authored native cleanup against artificial SQLite tables.
const { DatabaseSync } = require('node:sqlite');
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const db = new DatabaseSync(':memory:');
db.exec(`CREATE TABLE sample_session_core(id INTEGER, start_time INTEGER, end_time INTEGER, type_id INTEGER, sync_status INTEGER);
 CREATE TABLE hihealth_stat_day(id INTEGER, date INTEGER, stat_type INTEGER, sync_status INTEGER);
 CREATE TABLE sample_point_health(id INTEGER, start_time INTEGER, end_time INTEGER, type_id INTEGER, sync_status INTEGER);
 INSERT INTO sample_session_core VALUES (1,100,200,22101,2),(2,100,200,22101,1),(3,300,400,22101,2),(4,100,200,999,2);
 INSERT INTO hihealth_stat_day VALUES(1,20010101,44100,2),(2,20010102,44100,2),(3,20010101,999,2);
 INSERT INTO sample_point_health VALUES(1,150,150,90001,1),(2,150,150,90001,2),(3,350,350,90001,1),(4,150,150,999,1);`);
let committed = false, transactions = 0, failDelete = false;
const adapter = {
 isOpen: () => true, getPath: () => '/artificial/hihealth_003.db', $dispose: () => {},
 beginTransaction() { transactions++; committed = false; db.exec('BEGIN'); },
 setTransactionSuccessful() { committed = true; },
 endTransaction() { db.exec(committed ? 'COMMIT' : 'ROLLBACK'); },
 rawQuery: { overload: () => ({call(self, sql) {
  const row = db.prepare(sql).get();
  return {moveToFirst: () => true, getInt: () => Object.values(row)[0], close() {}};
 }}) },
 execSQL: { overload: () => ({call(self, sql) {
  if (failDelete && sql.includes('hihealth_stat_day')) throw new Error('Artificial DB failure');
  db.exec(sql);
 }}) }
};
const config = {record_day: 20010101, delete_scope: {start: 100, end: 200}, sleep_cloud_empty_verified: false};
const box = {config, rpc: {exports: {}}, nativeCall: fn => Promise.resolve().then(fn), Java: {
 choose(name, handlers) { handlers.onMatch(adapter); handlers.onComplete(); },
 retain: x => x, array: (name, x) => x,
 use: () => ({values: () => [{name: () => 'SLEEP_ON_OFF_BED_RECORD_BED_TIME', value: () => 90001}]})
}};
vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname, '../sleep_sync_lab/automation_native.js'), 'utf8'), box);
const ids = table => db.prepare('SELECT id FROM ' + table + ' ORDER BY id').all().map(r => r.id);
(async () => {
 const cleanup = box.rpc.exports.autoRetireAcknowledgedSleepDeletes;
 await assert.rejects(cleanup(100,200), /No verified cloud deletion/);
 assert.equal(transactions,0);
 config.sleep_cloud_empty_verified = true;
 await assert.rejects(cleanup(99,200), /No verified cloud deletion/);
 assert.equal(transactions,0);
 failDelete = true;
 await assert.rejects(cleanup(100,200), /Artificial DB failure/);
 assert.deepEqual(ids('sample_session_core'),[1,2,3,4]);
 assert.deepEqual(ids('sample_point_health'),[1,2,3,4]);
 failDelete = false;
 const result = await cleanup(100,200);
 assert.equal(result.resultCode,0);
 assert.deepEqual(ids('sample_session_core'),[2,3,4]);
 assert.deepEqual(ids('hihealth_stat_day'),[2,3]);
 assert.deepEqual(ids('sample_point_health'),[3,4]);
 process.stdout.write('Native scoped cleanup and rollback passed\n');
})().catch(e => { process.stderr.write(String(e)); process.exitCode=1; });
