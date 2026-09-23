"""The short socket proof must declare one feasible task per AMR."""

from src.scenarios import deployment_socket_acceptance


def test_deployment_socket_scenario_is_small_complete_and_isolated():
    scenario = deployment_socket_acceptance(n_robots=3, seed=0)

    assert scenario.n_robots == 3
    assert scenario.n_tasks == 3
    assert all(len(queue) == 1 for queue in scenario.assignments)
    for start, queue in zip(scenario.starts, scenario.assignments):
        task = queue[0]
        assert start[1] == task.pick[1] == task.drop[1]
        assert scenario.env.passable(start)
        assert scenario.env.passable(task.pick)
        assert scenario.env.passable(task.drop)
