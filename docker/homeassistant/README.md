# homeassistant

[Home Assistant](https://www.home-assistant.io) (`stable` image) on the host network, which
device discovery needs. UI on `192.168.0.10:8123`.

`configuration.yaml` is the default one. Integrations, devices, areas and dashboards are
set up in the UI and stored in `config/.storage`, which also holds login tokens,
integration credentials and the home's location - so `.storage` and `secrets.yaml` are in
the encrypted bundle, not here.
