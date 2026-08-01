import json
import threading
from app.db.session import SessionLocal
from app.models.import_task import ImportTask
from app.services.importer.import_engine import ImportEngine


class ImportService:
    def __init__(self):
        self.engine = ImportEngine()

    def _run(self, task_id: int, data: list[str]):
        db = SessionLocal()

        task = db.query(ImportTask).get(task_id)
        task.status = "running"
        task.progress = 0
        db.commit()

        def update_progress(value):
            task.progress = value
            db.commit()

        result = self.engine.process(data, update_progress)

        task.status = "done"
        task.progress = 100
        task.results = json.dumps(result)

        db.commit()
        db.close()

    def create_import(self, data: list[str]):
        db = SessionLocal()

        task = ImportTask(status="pending", progress=0)
        db.add(task)
        db.commit()
        db.refresh(task)

        thread = threading.Thread(target=self._run, args=(task.id, data))
        thread.start()

        return task