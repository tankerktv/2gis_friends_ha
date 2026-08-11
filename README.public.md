# 2GIS Friends for Home Assistant

Puts the people who share their location with you in 2GIS on your Home Assistant
map. Every friend gets a map marker, a phone battery reading and a "last seen"
timestamp.

*[Русская версия](README.ru.md)*

2GIS is a mapping service widely used across Russia and the CIS. Its mobile app
has a "Friends on map" feature; this integration brings that data into Home
Assistant. The Home Assistant UI is available in both English and Russian.

---

## Requirements

* Home Assistant **2024.12** or newer
* a 2GIS account with friends sharing their location with you

The integration icon appears on Home Assistant 2026.3 and newer.

## Installation

**Via HACS** (recommended):

1. HACS → **Integrations** → ⋮ → **Custom repositories**
2. add `https://github.com/tankerktv/2gis_friends_ha`, category **Integration**
3. find "2GIS Friends" → **Download**
4. restart Home Assistant

**Manually:** copy the `custom_components/twogis_friends` folder into
`config/custom_components/` and restart Home Assistant.

## Setup

**Settings → Devices & Services → Add Integration → 2GIS Friends**

You will be asked for an access token. Here is how to get it:

1. open [2gis.ru](https://2gis.ru) and sign in
2. press **F12** → **Network** tab → click the **WS** filter
3. reload the page and wait for a `user/ws` row to appear
4. click it → **Headers** → **Request URL**
5. copy the value of the `token=` parameter — 40 characters, digits and letters a–f

The token is verified immediately, so a typo shows up right away. When it
eventually expires, Home Assistant will ask you for a new one.

## What you get

Each friend becomes a device with three entities:

| Entity | Shows |
|---|---|
| `device_tracker` | position on the map |
| `sensor` "Battery" | phone charge, in percent |
| `sensor` "Last seen" | when data last arrived |

Your own account shows up too, so you can track yourself without any extra setup.

The tracker also carries attributes: whether the friend is moving or stationary,
whether the phone is charging, speed, heading, and how 2GIS classifies the place
(home, work).

## Options

**Settings → Devices & Services → 2GIS Friends → Configure**

**Viewport radius.** 2GIS only sends updates for friends inside a map viewport.
That viewport is a square around your Home Assistant coordinates, 2° by default
(roughly 220 km in each direction). A friend outside it simply stops updating.

**Reconnect after silence.** If nothing arrives from 2GIS for a while, the
integration rebuilds the connection. Default is 8 minutes.

## Things worth knowing

**Updates are pushed, not polled.** The integration never polls on a schedule —
2GIS sends data when it changes, roughly every 4 minutes for someone standing
still. That is why there is no "scan interval" option.

**Coordinates can be stale.** When a friend stops sharing their location, 2GIS
keeps returning their **last known** position. On the map this looks like the
person is standing still right now, while the data may be hours old. You can
tell the difference from the "Last seen" sensor, or from the `movement`
attribute — stale points have it set to `noGeo`.

**History starts when you install.** 2GIS offers no archive, so you cannot see
where a friend was before the integration was set up.

**Nothing leaves your network.** The integration talks only to 2GIS servers;
everything else stays inside your Home Assistant.

## Showing movement history

The built-in map card can draw a trail for the last few hours:

```yaml
type: map
entities:
  - device_tracker.friend_name
hours_to_show: 24
auto_fit: true
```

To browse a specific day, the third-party
[Location Timeline Card](https://github.com/konewka17/timeline_card) from HACS
works well — these entities are compatible with it.

## Troubleshooting

Check **Settings → System → Logs** and search for `twogis_friends`.

Common cases:

* **coordinates frozen** — look at the "Last seen" sensor; if it is stale too,
  the integration lost its connection, and reloading the integration helps;
* **a friend is missing** — either they are not sharing their location with you,
  or they are outside the viewport, see Options;
* **asked for a new token** — the old one stopped working, get a fresh one the
  same way you did during setup.

## License

MIT — see [LICENSE](LICENSE).
