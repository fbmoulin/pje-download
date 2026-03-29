import sys

sys.path.insert(0, ".")
from dashboard_api import DashboardState
from pathlib import Path

print("Imports OK")
state = DashboardState(Path("./downloads_batch"))
print(f"Batches carregados do historico: {len(state.batches)}")
for bid, job in list(state.batches.items())[:3]:
    print(f"  {bid}: {len(job.processos)} processos, status={job.status}")
print("Dashboard API pronta!")
