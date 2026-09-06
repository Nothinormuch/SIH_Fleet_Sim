// Convert socket telemetry into the existing renderer's schema without running a planner.
export function frameFromSnapshot(snapshot, result = null) {
  return {
    ...snapshot.world,
    completed_task_ids: result?.completed_task_ids || snapshot.observed_completed,
    fleet: snapshot.nodes.map(node => {
      const status = node.visual_status || {};
      return {
        ...status, id: node.id,
        state: node.command.safety_stop ? 'blocked' : status.state || 'unknown',
        path: Array.isArray(status.path) ? status.path : [],
        // Unknown cargo stays unknown; do not infer pickup from robot position.
        carry: typeof status.carry === 'string' ? status.carry : null,
      };
    }),
  };
}

export function sceneFromSnapshot(snapshot) {
  return {
    map: snapshot.map,
    meta: {cell_m: snapshot.cell_m, tasks_catalog: snapshot.tasks, dead_zones: [], live: true},
    frames: [frameFromSnapshot(snapshot)],
  };
}

export function interpolateLiveFrame(before, after, targetTime) {
  const span = after.t - before.t;
  const f = span > 0 ? Math.max(0, Math.min(1, (targetTime - before.t) / span)) : 1;
  const older = new Map(before.robots.map(robot => [robot.id, robot]));
  const stopped = new Set(after.fleet.filter(node => node.state === 'blocked').map(node => node.id));
  return {
    ...after, t: before.t + Math.max(0, span) * f,
    robots: after.robots.map(robot => {
      const previous = older.get(robot.id);
      if (!previous || stopped.has(robot.id)) return robot;
      const rotation = Math.atan2(Math.sin(robot.th - previous.th), Math.cos(robot.th - previous.th));
      return {...robot, x: previous.x + (robot.x - previous.x) * f,
        y: previous.y + (robot.y - previous.y) * f, th: previous.th + rotation * f};
    }),
  };
}
