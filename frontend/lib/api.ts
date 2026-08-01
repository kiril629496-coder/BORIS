const API_URL = "http://localhost:8000";

export async function startImport() {
  const res = await fetch(`${API_URL}/api/import`, {
    method: "POST",
  });

  return res.json();
}

export async function getImportStatus(taskId: string) {
  const res = await fetch(`${API_URL}/api/import/${taskId}`);
  return res.json();
}