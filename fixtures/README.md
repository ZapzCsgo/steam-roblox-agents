# Fixtures

- `http/<host>/<key>.json`: real responses from public Steam, SteamCharts and Roblox
  endpoints, recorded with `sra record <appid>` (demo candidates) and
  `python -m sra.evaluation --record` (store items of the 21 historical cases). Each file
  keeps the request URL, parameters and fetch time; the key is a hash of URL and stable
  parameters. Replay mode (`SRA_HTTP_MODE=replay`, the default) reads only these files.
- `llm/demo.json`: scripted Analyst answers for the offline demo. They are hand-written
  stand-ins for a model, not recorded model output. The first answer for Moon Sweepers
  deliberately breaks consistency rule C1 (F3=4 with an Open World tag) so the demo shows
  the Critic sending it back.
