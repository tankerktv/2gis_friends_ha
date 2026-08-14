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

Each friend becomes a device with six entities:

| Entity | Shows |
|---|---|
| `device_tracker` | position on the map |
| `sensor` "Battery" | phone charge, in percent — the icon switches to the charging one |
| `sensor` "Last seen" | when data last arrived |
| `binary_sensor` "At home" | whether 2GIS places the friend at their own home |
| `binary_sensor` "Charging" | whether the phone is on a charger |
| `binary_sensor` "Data is stale" | whether the coordinates can still be trusted |

Your own account shows up too, so you can track yourself without any extra setup.

**"At home" is about the friend's own home, not your Home Assistant zones.**
2GIS knows the places a person visits often and labels one of them as home.
A friend can be at their own home and still show as `not_home` in the tracker —
these are two different questions, and both are useful.

**"Data is stale" is the one to check first when something looks odd.** It turns
on when 2GIS reports `noGeo`, meaning the friend stopped sharing and you are
looking at their last known position. Note that "At home" keeps its last value
in that case too, so read the two together.

A separate device for the integration itself carries one more entity:

| Entity | Shows |
|---|---|
| `binary_sensor` "2GIS connection" | whether the socket to 2GIS is alive |

**Together with "Data is stale" it answers whose problem it is** — a distinction
that matters, because the two are fixed differently:

| Connection | Data is stale | What happened |
|---|---|---|
| on | on | the friend stopped sharing — nothing you can do |
| off | — | our connection dropped — reloading the integration helps |

This entity **stays available while the connection is down** — otherwise it
would be useless at exactly the moment you need it.

The tracker also carries attributes: whether the friend is moving or stationary,
speed, heading, and the raw place label from 2GIS.

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

## A friend appears twice

The same person shows up as two devices: one live, one permanently
"unavailable" and with fewer entities.

**Why.** A friend's id in 2GIS is not permanent — it changes when the person
reinstalls the app or signs in with a different account. To the integration
that is a new friend, so a new device appears and the old one stays. Nothing
can prevent this; it happens on the 2GIS side.

**How to remove it.** Settings → Devices & Services → 2GIS Friends → open the
dead device → ⋮ → **Delete**. The live one cannot be deleted — the next update
would just recreate it.

**How to keep the history.** History is tied to the entity id, not to the
device. The order matters — get it wrong and the live friend's history is
orphaned.

1. **First** rename the **dead** device's entities to free the good name:
   `device_tracker.friend_name` → `device_tracker.friend_name_old`.
   Its history follows the rename.
2. Delete the dead device.
3. Now rename the live device's entities to the freed name (they most likely
   carry a `_2` suffix right now). Their history follows too.

The live friend ends up under a clean name with all of their history. The old
one's history stays under `_old` and eventually goes away with the regular
database purge.

> **Why you cannot just delete and rename.** Removing an entity from the
> registry **does not remove its history** — the rows stay in the database
> until the regular age-based purge clears them. The name therefore stays
> taken, and on rename the recorder checks for that and **refuses** to migrate
> the history:
>
> ```
> Cannot migrate history for entity_id `…` to `…`
> because the new entity_id is already in use
> ```
>
> The rename itself still goes through, but the live friend's history stays
> behind under the old `_2` id and disappears from the UI. If you see that
> warning in the log, the order was wrong.

> **The two histories cannot be merged into a single timeline** — not through
> the UI and not through settings. To the database they are two different
> entities. Merging them would mean editing the recorder database directly.

## License

MIT — see [LICENSE](LICENSE).
