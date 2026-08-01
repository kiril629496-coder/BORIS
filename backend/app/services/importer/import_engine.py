import time


class ImportEngine:
    def process(self, items: list[str], update_progress):
        results = []

        total = len(items)

        for i, item in enumerate(items):
            time.sleep(1)

            results.append({
                "index": i,
                "value": item,
                "status": "processed"
            })

            progress = int((i + 1) / total * 100)
            update_progress(progress)

        return results