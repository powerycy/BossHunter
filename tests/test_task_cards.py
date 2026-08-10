import unittest

from bosshunter.web.tasks import TaskAlreadyRunningError, WorkbenchTask, WorkbenchTaskRunner


class TaskCardRunnerTests(unittest.TestCase):
    def test_stopped_delivery_task_can_resume_its_original_job_ids(self):
        calls = []
        runner = WorkbenchTaskRunner({
            "deliver": lambda task, config: calls.append(config.get("_workbench_job_ids")),
        })
        stopped = WorkbenchTask(id="old", mode="deliver", label="deliver", status="stopped")
        stopped.context["resume"] = {"mode": "deliver", "job_ids": ["job-a", "job-b"]}
        runner._tasks[stopped.id] = stopped

        resumed = runner.resume(stopped.id, {})
        runner.wait(timeout=1)

        self.assertEqual(resumed["mode"], "deliver")
        self.assertEqual(calls, [["job-a", "job-b"]])
        self.assertTrue(resumed["id"] != stopped.id)

    def test_terminal_task_can_be_deleted_without_touching_jobs(self):
        runner = WorkbenchTaskRunner()
        task = WorkbenchTask(id="done", mode="collect", label="collect", status="stopped")
        runner._tasks[task.id] = task

        result = runner.delete(task.id)

        self.assertEqual(result, {"id": "done", "deleted": True})
        self.assertIsNone(runner.status()["last_task"])

    def test_active_task_cannot_be_deleted(self):
        runner = WorkbenchTaskRunner()
        task = WorkbenchTask(id="running", mode="collect", label="collect", status="running")
        runner._tasks[task.id] = task

        with self.assertRaises(TaskAlreadyRunningError):
            runner.delete(task.id)


if __name__ == "__main__":
    unittest.main()
