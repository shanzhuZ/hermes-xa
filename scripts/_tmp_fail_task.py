# -*- coding: utf-8 -*-
from collect_01 import db

tid = "e2606d80-7bd8-4d9d-be48-e6add8915b07"
db.execute(
    """
    UPDATE hermes_tasks
    SET status='failed',
        finished_at=COALESCE(finished_at, NOW(3)),
        updated_at=NOW(3)
    WHERE task_id=%s AND status='running'
    """,
    (tid,),
)
print(db.fetch_one("SELECT status FROM hermes_tasks WHERE task_id=%s", (tid,)))
