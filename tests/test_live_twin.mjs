import test from 'node:test';
import assert from 'node:assert/strict';
import {frameFromSnapshot, sceneFromSnapshot, interpolateLiveFrame} from '../frontend/js/live-twin-data.js';

const snapshot = {
  world:{t:1, robots:[{id:'AMR01',x:1,y:2,th:0}], humans:[], obstacles:[]},
  map:{width:4,height:4,grid:[]}, cell_m:1.4,
  tasks:[{id:'T1',pick:[1,1],drop:[2,2]}], observed_completed:[],
  nodes:[{id:'AMR01',command:{safety_stop:false},
    visual_status:{id:'AMR01',state:'to_drop',carry:'T1',task:'T1',path:[[2,2]]}}],
};

test('scene adapter preserves the real map, metre positions, and node cargo',()=>{
  const scene=sceneFromSnapshot(snapshot), frame=scene.frames[0];
  assert.equal(scene.map,snapshot.map);
  assert.equal(scene.meta.cell_m,1.4);
  assert.deepEqual(frame.robots,snapshot.world.robots);
  assert.equal(frame.fleet[0].carry,'T1');
  assert.equal(frame.fleet[0].state,'to_drop');
  assert.deepEqual(snapshot.observed_completed,[]);
});

test('safety stop takes precedence over a stale to-drop status',()=>{
  const cut=structuredClone(snapshot); cut.nodes[0].command.safety_stop=true;
  assert.equal(frameFromSnapshot(cut).fleet[0].state,'blocked');
});

test('missing telemetry does not invent cargo or a task owner',()=>{
  const missing=structuredClone(snapshot); delete missing.nodes[0].visual_status;
  const node=frameFromSnapshot(missing).fleet[0];
  assert.equal(node.carry,null); assert.equal(node.state,'unknown'); assert.equal(node.task,undefined);
});

test('final node completions replace passive announcements, including an empty result',()=>{
  const input={...snapshot, observed_completed:['ANNOUNCED']};
  assert.deepEqual(frameFromSnapshot(input,{completed_task_ids:[]}).completed_task_ids,[]);
  assert.deepEqual(frameFromSnapshot(input,{completed_task_ids:['T1']}).completed_task_ids,['T1']);
});

test('interpolation is bounded to measured positions and rotates by the short arc',()=>{
  const before=frameFromSnapshot(snapshot);
  const after=structuredClone(before); after.t=2;after.robots[0].x=3;
  before.robots=[{...before.robots[0],th:Math.PI-.1}];after.robots[0].th=-Math.PI+.1;
  const mid=interpolateLiveFrame(before,after,1.5);
  assert.equal(mid.robots[0].x,2);assert.ok(Math.abs(mid.robots[0].th-Math.PI)<1e-10);
  assert.equal(interpolateLiveFrame(before,after,200).robots[0].x,3);
  assert.equal(interpolateLiveFrame(before,after,-10).robots[0].x,1);
  after.fleet[0].state='blocked';
  assert.equal(interpolateLiveFrame(before,after,1.1).robots[0].x,3);
});
