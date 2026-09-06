# Charts

Static figures rendered from **aggregates already published** in this archive
(`analysis/timeline.md`, `signature-sweep.md`, `sub-swarms.md`, README wiki table).
No re-hosted export rows — numbers only.

| Figure | File | Source |
|---|---|---|
| Revision volume (staging → burst → collapse) | [`01-timeline-volume.png`](01-timeline-volume.png) | [timeline](../analysis/timeline.md) |
| Detection signature classes | [`02-signature-classes.png`](02-signature-classes.png) | [signature-sweep](../analysis/signature-sweep.md) |
| Revisions by wiki (ProWiki cut) | [`03-wiki-split.png`](03-wiki-split.png) | [README](../README.md) |
| Task sub-swarms by revisions | [`04-task-subswarms.png`](04-task-subswarms.png) | [sub-swarms](../analysis/sub-swarms.md) |
| Shared substrate layers | [`05-infra-layers.png`](05-infra-layers.png) | [sub-swarms](../analysis/sub-swarms.md) |

Also see the interactive coordination graph: [`graph.html`](../graph.html).

Regenerate (from a checkout that has the published markdown only):

```bash
# data are hard-coded from the published tables in scripts/render_charts.py
python scripts/render_charts.py
```
