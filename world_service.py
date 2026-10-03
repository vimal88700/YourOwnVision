from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from database import Database, DatabaseConflict, DatabaseError

# Small deterministic content library. No per-click AI call, no asset download,
# and no server-side story object kept in RAM. The world seed + player state
# produce the next situation deterministically.
DIMENSIONS = [
    ("ordinary", "The Living City"),
    ("coast", "The Salt Coast"),
    ("underworld", "The Underline"),
    ("mirror", "The Mirror District"),
    ("dream", "The Dreaming Quarter"),
    ("digital", "The Glass Network"),
    ("frontier", "The Far Frontier"),
    ("wild", "The Wild Country"),
]

LOCATIONS = [
    ("market", "Old Market", "ordinary"),
    ("station", "Central Station", "ordinary"),
    ("rooftops", "North Rooftops", "ordinary"),
    ("harbor", "Night Harbor", "coast"),
    ("lighthouse", "The Broken Lighthouse", "coast"),
    ("tunnels", "The Service Tunnels", "underworld"),
    ("archive", "The Buried Archive", "underworld"),
    ("mirror_square", "Mirror Square", "mirror"),
    ("glass_house", "The Glass House", "mirror"),
    ("sleeping_hotel", "The Sleeping Hotel", "dream"),
    ("red_road", "The Red Road", "frontier"),
    ("orchard", "The Silent Orchard", "wild"),
    ("relay", "Relay Nine", "digital"),
    ("server_garden", "The Server Garden", "digital"),
]

NPCS = [
    ("mara", "Mara Vale", "streetwise fixer"),
    ("jonah", "Jonah Reed", "quiet investigator"),
    ("sana", "Sana Iqbal", "restless courier"),
    ("elias", "Elias North", "retired cartographer"),
    ("rhea", "Rhea Moss", "mechanic with a secret"),
    ("noor", "Noor Sen", "musician who remembers impossible places"),
    ("keeper", "The Keeper", "ageless archivist"),
    ("cass", "Cass Rowan", "charismatic drifter"),
    ("ivy", "Ivy Mercer", "doctor who asks dangerous questions"),
    ("ren", "Ren Sol", "traveler who knows the shortcuts"),
]

SITUATIONS = [
    ("A stranger leaves a key on your table", "mystery"),
    ("A locked door appears where there was only a wall", "mystery"),
    ("Someone you trust asks for a dangerous favor", "relationship"),
    ("A crowd suddenly runs in the opposite direction", "danger"),
    ("A forgotten message arrives from your own future", "mystery"),
    ("A small object starts changing when you hold it", "wonder"),
    ("Two factions offer incompatible promises", "faction"),
    ("An injured traveler recognizes you before you meet", "relationship"),
    ("The local rules stop making sense", "dimension"),
    ("You find a safe place that may not stay safe", "survival"),
    ("A quiet conversation reveals a hidden motive", "social"),
    ("Something valuable is offered for a personal secret", "choice"),
    ("A path opens only if you give something up", "sacrifice"),
    ("A familiar face appears in a completely wrong place", "relationship"),
    ("The world shows you another possible life", "dream"),
    ("A celebration hides a private negotiation", "social"),
    ("Someone claims they remember a life you never lived", "identity"),
    ("A harmless promise quietly changes the rules around you", "consequence"),
    ("A hidden settlement offers you a place to belong", "community"),
    ("You discover that another player has changed this place", "multiplayer"),
]


OPENERS = [
    "The first thing you notice is that nobody is acting surprised.",
    "You arrive a moment before the important part begins.",
    "Something about the silence feels deliberate.",
    "A small detail refuses to fit the version of events you were given.",
    "For once, the safest-looking option is also the strangest.",
    "You have the uncomfortable feeling that this moment has happened before.",
    "Someone nearby is pretending not to watch you.",
    "The world seems ordinary until one impossible detail catches your eye.",
    "A choice is already waiting for you before anyone says there is a choice.",
    "You can feel several possible futures narrowing around this moment.",
    "The local story sounds simple. The evidence does not.",
    "Nobody asks you to get involved. That is exactly why you should worry.",
]

TWISTS = [
    "A second clue points somewhere completely different.",
    "The person who should be afraid is oddly calm.",
    "A familiar symbol appears where it should not exist.",
    "Someone offers help without asking for anything in return.",
    "The safest explanation turns out to be the least useful one.",
    "A distant sound makes everyone pause at the same time.",
    "One of the objects here seems to remember being somewhere else.",
    "The boundary between this place and another world flickers for a heartbeat.",
    "A stranger quietly uses your name even though you never introduced yourself.",
    "A promise made here may matter much later.",
    "You spot another player's influence in the aftermath of an earlier decision.",
    "There is a route forward, but it costs you something you may want later.",
]

CHOICES = [
    ("Approach carefully", {"courage": 1, "risk": 1}, "You step closer without revealing what you know."),
    ("Listen before acting", {"insight": 1, "trust": 1}, "You wait long enough to hear the detail everyone else missed."),
    ("Take the opportunity", {"luck": 1, "risk": 2}, "You gamble on the opening before it disappears."),
    ("Protect the person involved", {"empathy": 1, "trust": 2}, "You put another person's safety ahead of your advantage."),
    ("Walk away and observe", {"insight": 2, "risk": -1}, "You give the situation room to reveal itself."),
    ("Tell the truth", {"honesty": 2, "trust": 1}, "You say the part that would have been easier to hide."),
    ("Keep the secret", {"cunning": 1, "trust": -1}, "You keep the information close and accept the cost."),
    ("Make a deal", {"cunning": 1, "luck": 1}, "You trade something useful for a chance to move forward."),
    ("Search for another route", {"insight": 1, "luck": 1}, "You refuse the obvious path and look for a third option."),
    ("Trust your instinct", {"courage": 1, "empathy": 1, "risk": 1}, "You act before certainty arrives."),
    ("Ask for help", {"empathy": 1, "trust": 2}, "You let someone else become part of the decision."),
    ("Break the rule", {"courage": 2, "risk": 3}, "You deliberately cross a line that was supposed to stay untouched."),
]

CONVERGENCE_EVERY = 5


class WorldService:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def iso(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat()

    @staticmethod
    def h(*parts: Any) -> int:
        raw = "|".join(map(str, parts)).encode("utf-8")
        return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")

    @classmethod
    def pick(cls, values: list[Any], key: int) -> Any:
        return values[key % len(values)]

    @classmethod
    def dimension_name(cls, key: str) -> str:
        return next((name for ident, name in DIMENSIONS if ident == key), "The Unknown")

    @classmethod
    def location(cls, location_id: str) -> tuple[str, str, str]:
        return next((item for item in LOCATIONS if item[0] == location_id), LOCATIONS[0])

    @classmethod
    def initial_player_state(cls, user_id: int) -> dict[str, Any]:
        # Deliberately compact. We do not keep a transcript or dead character history.
        return {
            "status": "alive",
            "lives": 3,
            "health": 100,
            "energy": 100,
            "turns": 0,
            "deaths": 0,
            "cycle": 0,
            "stats": {"courage": 0, "insight": 0, "luck": 0, "empathy": 0, "honesty": 0, "cunning": 0},
            "inventory": [],
            "relationships": {},
            "flags": {},
            "checkpoint": {"location_id": "market", "dimension": "ordinary"},
            "last_choice": None,
            "path": {"chapter": 0, "node": "intro", "branch": 0},
            "user_id": user_id,
        }

    @classmethod
    def initial_world_state(cls, seed: int) -> dict[str, Any]:
        loc = cls.pick(LOCATIONS, seed)
        return {
            "seed": seed,
            "global_events": 0,
            "convergence": 0,
            "arc": 0,
            "threat": 0,
            "location_id": loc[0],
            "location_name": loc[1],
            "dimension": loc[2],
            "dimension_name": cls.dimension_name(loc[2]),
            "weather": cls.pick(["clear", "rain", "wind", "fog", "heat", "quiet", "electric"], seed // 7),
            "flags": {},
            "discovered_dimensions": [loc[2]],
            "discovered_locations": [loc[0]],
            "used_situations": [],
            "used_npcs": [],
            "shared_node": "intro",
        }

    async def create_world(self, creator_id: int, chat_id: int | None = None, title: str | None = None) -> dict[str, Any]:
        seed = secrets.randbits(62)
        deadline = self.now() + timedelta(seconds=45)
        clean_title = (title or "WHAT HAPPENS?").strip()[:80] or "WHAT HAPPENS?"
        rows = await self.db.request(
            "POST", "world_games", params={"select": "*"},
            json={
                "creator_id": creator_id,
                "chat_id": chat_id,
                "title": clean_title,
                "status": "waiting",
                "join_deadline": self.iso(deadline),
                "seed": seed,
                "world_state": self.initial_world_state(seed),
                "settings": {"allow_external_invites": True},
                "last_event": {"type": "world_created"},
                "version": 1,
                "max_players": 20,
            },
            prefer="return=representation",
        )
        if not rows:
            raise DatabaseError("Could not create world.")
        await self.event(rows[0]["id"], creator_id, "world_created", {"title": clean_title})
        return rows[0]

    async def get_world(self, game_id: str) -> dict[str, Any]:
        rows = await self.db.request("GET", "world_games", params={"id": f"eq.{game_id}", "limit": "1"})
        if not rows:
            raise ValueError("World not found.")
        return rows[0]

    async def get_group_world(self, chat_id: int) -> dict[str, Any] | None:
        rows = await self.db.request(
            "GET", "world_games",
            params={"chat_id": f"eq.{chat_id}", "status": "in.(waiting,active)", "order": "created_at.desc", "limit": "1"},
        )
        if not rows:
            return None
        game = rows[0]
        # If a world was left unused in the lobby, reuse it instead of creating another row.
        if game["status"] == "waiting" and game.get("join_deadline"):
            deadline = datetime.fromisoformat(str(game["join_deadline"]).replace("Z", "+00:00"))
            if self.now() >= deadline and not await self.players(game["id"]):
                new_deadline = self.now() + timedelta(seconds=45)
                updated = await self.db.request(
                    "PATCH", "world_games", params={"id": f"eq.{game['id']}", "status": "eq.waiting"},
                    json={"join_deadline": self.iso(new_deadline), "last_event": {"type": "lobby_reopened"}, "version": int(game.get("version") or 1) + 1},
                    prefer="return=representation",
                )
                if updated:
                    game = updated[0]
        return game

    async def get_player(self, game_id: str, user_id: int) -> dict[str, Any] | None:
        rows = await self.db.request("GET", "world_players", params={"game_id": f"eq.{game_id}", "telegram_user_id": f"eq.{user_id}", "limit": "1"})
        return rows[0] if rows else None

    async def players(self, game_id: str) -> list[dict[str, Any]]:
        return await self.db.request("GET", "world_players", params={"game_id": f"eq.{game_id}", "order": "joined_at.asc"})

    async def join(self, game_id: str, user_id: int, username: str, display_name: str) -> dict[str, Any]:
        game = await self.get_world(game_id)
        if game["status"] not in ("waiting", "active"):
            raise ValueError("This world is no longer accepting players.")

        if game["status"] == "waiting" and game.get("join_deadline"):
            deadline = datetime.fromisoformat(str(game["join_deadline"]).replace("Z", "+00:00"))
            if self.now() >= deadline:
                existing = await self.players(game_id)
                if existing:
                    game = await self.start(game_id)
                else:
                    # Reuse this unstarted world rather than throwing it away.
                    await self.db.request(
                        "PATCH", "world_games",
                        params={"id": f"eq.{game_id}", "status": "eq.waiting"},
                        json={"join_deadline": self.iso(self.now() + timedelta(seconds=45)), "version": int(game.get("version") or 1) + 1},
                        prefer="return=minimal",
                    )

        existing = await self.get_player(game_id, user_id)
        if existing:
            return existing

        rows = await self.db.rpc("join_world_atomic", {
            "p_game_id": game_id,
            "p_user_id": user_id,
            "p_username": username or "",
            "p_display_name": display_name or "Player",
        })
        if not rows:
            raise DatabaseConflict("Could not join this world.")
        player = rows[0] if isinstance(rows, list) else rows
        await self.event(game_id, user_id, "player_joined", {"display_name": display_name or "Player"})
        return player

    async def start(self, game_id: str) -> dict[str, Any]:
        game = await self.get_world(game_id)
        if game["status"] == "active":
            return game
        if game["status"] != "waiting":
            return game
        if not await self.players(game_id):
            raise ValueError("No players have joined yet.")
        rows = await self.db.request(
            "PATCH", "world_games",
            params={"id": f"eq.{game_id}", "status": "eq.waiting"},
            json={"status": "active", "started_at": self.iso(self.now()), "version": int(game.get("version") or 1) + 1, "last_event": {"type": "world_started"}},
            prefer="return=representation",
        )
        return rows[0] if rows else await self.get_world(game_id)

    async def activate_due_worlds(self) -> list[str]:
        try:
            result = await self.db.rpc("activate_due_worlds", {})
            # RPC returns integer in JSON form.
        except Exception:
            return []
        if not result:
            return []
        rows = await self.db.request("GET", "world_games", params={"status": "eq.active", "order": "started_at.desc", "limit": "50"})
        return [str(row["id"]) for row in rows]

    async def create_invite(self, game_id: str, user_id: int) -> str:
        token = secrets.token_urlsafe(12).replace("-", "_")[:24]
        await self.db.request(
            "POST", "world_invites", params={"select": "*"},
            json={"token": token, "game_id": game_id, "created_by": user_id, "uses": 0, "max_uses": 100, "active": True},
            prefer="return=representation",
        )
        return token

    async def consume_invite(self, token: str, user_id: int, username: str, name: str) -> dict[str, Any]:
        rows = await self.db.request("GET", "world_invites", params={"token": f"eq.{token}", "active": "eq.true", "limit": "1"})
        if not rows:
            raise ValueError("Invite link is invalid or expired.")
        invite = rows[0]
        if int(invite["uses"]) >= int(invite["max_uses"]):
            raise ValueError("This invite has reached its limit.")
        player = await self.join(str(invite["game_id"]), user_id, username, name)
        await self.db.request("PATCH", "world_invites", params={"token": f"eq.{token}"}, json={"uses": int(invite["uses"]) + 1})
        return {"game": await self.get_world(str(invite["game_id"])), "player": player}

    @classmethod
    def _next_location(cls, seed: int, key: int, current_dimension: str | None = None) -> tuple[str, str, str]:
        candidates = [x for x in LOCATIONS if x[2] != current_dimension] or LOCATIONS
        return cls.pick(candidates, key)

    @classmethod
    def _scene_for(cls, game: dict[str, Any], player: dict[str, Any]) -> dict[str, Any]:
        world = dict(game.get("world_state") or {})
        state = dict(player.get("state") or {})
        seed = int(world.get("seed") or game.get("seed") or 1)
        turns = int(state.get("turns") or 0)
        cycle = int(state.get("cycle") or 0)
        uid = int(player["telegram_user_id"])
        shared_node = str(world.get("shared_node") or "intro")
        loc = cls.location(str(world.get("location_id") or "market"))
        npc = cls.pick(NPCS, cls.h(seed, turns, cycle, "npc", shared_node, loc[0]))
        situation = cls.pick(SITUATIONS, cls.h(seed, turns, cycle, uid, "situation", shared_node, loc[0]))
        used = set(world.get("used_situations") or [])
        # Avoid repeating the exact situation key until the compact bank has been exhausted.
        if f"{situation[0]}:{loc[0]}:{cycle}" in used:
            for offset in range(1, len(SITUATIONS)):
                candidate = SITUATIONS[(cls.h(seed, turns, cycle, uid, "situation2", shared_node, loc[0]) + offset) % len(SITUATIONS)]
                key = f"{candidate[0]}:{loc[0]}:{cycle}"
                if key not in used:
                    situation = candidate
                    break
        bond = int((state.get("relationships") or {}).get(npc[0], 0))
        convergence = turns > 0 and turns % CONVERGENCE_EVERY == 0
        if convergence:
            title = "The place where paths meet"
            text = (
                f"Everyone's separate path bends toward {loc[1]}. "
                f"{npc[1]} is there too, watching the group arrive from different directions. "
                "For a moment, the world feels shared rather than personal."
            )
            category = "convergence"
        else:
            title = f"{loc[1]} — {situation[0]}"
            opener = cls.pick(OPENERS, cls.h(seed, turns, cycle, uid, "opener", loc[0], situation[1]))
            twist = cls.pick(TWISTS, cls.h(seed, turns, cycle, uid, "twist", loc[0], situation[1]))
            text = (
                f"{opener} In {loc[1]}, {situation[0].lower()}. "
                f"{npc[1]} is nearby, a {npc[2]}. "
                f"The air feels {world.get('weather','quiet')}, and the boundary of "
                f"{cls.dimension_name(loc[2])} feels unusually thin. {twist}"
            )
            if bond >= 3:
                text += f" {npc[1]} clearly trusts you more than before."
            elif bond <= -3:
                text += f" {npc[1]} keeps a careful distance from you."
        base = cls.h(seed, turns, cycle, uid, shared_node, loc[0], situation[1])
        choices = []
        for i in range(3):
            label, effects, consequence = CHOICES[(base + i * 3) % len(CHOICES)]
            risk = ((base >> (i * 7)) % 5) - 1
            choices.append({
                "id": f"c{i+1}",
                "label": label,
                "preview": consequence,
                "risk": int(risk),
                "effects": effects,
                "npc_id": npc[0],
            })
        scene_id = f"{world.get('arc',0)}:{cycle}:{turn}:{shared_node}:{loc[0]}:{situation[1]}"
        return {
            "id": scene_id,
            "title": title,
            "text": text,
            "category": category,
            "location": loc[1],
            "dimension": cls.dimension_name(loc[2]),
            "npc": {"id": npc[0], "name": npc[1], "bond": bond},
            "convergence": convergence,
            "choices": choices,
        }

    def present(self, game: dict[str, Any], player: dict[str, Any] | None, players: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        world = game.get("world_state") or {}
        state = (player or {}).get("state") or {}
        roster = players or []
        return {
            "game": {
                "id": game["id"],
                "title": game["title"],
                "status": game["status"],
                "version": int(game.get("version") or 1),
                "max_players": int(game.get("max_players") or 20),
                "join_deadline": game.get("join_deadline"),
                "started_at": game.get("started_at"),
                "chat_id": game.get("chat_id"),
                "settings": game.get("settings") or {"allow_external_invites": True},
            },
            "player": None if not player else {
                "id": player["id"],
                "display_name": player["display_name"],
                "username": player.get("username", ""),
                "status": player.get("status", "alive"),
                "state": state,
            },
            "players": [
                {"id": p["id"], "display_name": p["display_name"], "username": p.get("username", ""), "status": p.get("status", "alive"), "joined_at": p.get("joined_at")}
                for p in roster
            ],
            "world": {
                "location": world.get("location_name"),
                "dimension": world.get("dimension_name"),
                "turn": int(state.get("turns") or 0),
                "global_events": int(world.get("global_events") or 0),
                "convergence": int(world.get("convergence") or 0),
                "threat": int(world.get("threat") or 0),
                "discoveries": list(world.get("discovered_dimensions") or [])[-8:],
            },
            "scene": self._scene_for(game, player) if player and game["status"] == "active" and state.get("status") == "alive" else None,
        }

    async def snapshot(self, game_id: str, user_id: int, include_unjoined: bool = False) -> dict[str, Any]:
        game = await self.get_world(game_id)
        player = await self.get_player(game_id, user_id)
        if not player and not include_unjoined:
            raise ValueError("Join this world first.")
        return self.present(game, player, await self.players(game_id))

    async def choose(self, game_id: str, user_id: int, choice_id: str, expected_version: int) -> dict[str, Any]:
        game = await self.get_world(game_id)
        if game["status"] == "waiting":
            game = await self.start(game_id)
        if game["status"] != "active":
            raise ValueError("This world is not active.")
        player = await self.get_player(game_id, user_id)
        if not player:
            raise ValueError("Join this world first.")
        if player.get("status") != "alive":
            raise ValueError("This character is currently out of the world. Re-enter from the world menu.")
        version = int(game.get("version") or 1)
        # A multiplayer click by another player must not invalidate this player's
        # choice. Refresh the shared world version and recompute the same scene.
        if int(expected_version) != version:
            game = await self.get_world(game_id)
            version = int(game.get("version") or 1)
            player = await self.get_player(game_id, user_id) or player

        scene = self._scene_for(game, player)
        choice = next((c for c in scene["choices"] if c["id"] == choice_id), None)
        if not choice:
            raise ValueError("That choice is no longer available.")

        world = dict(game.get("world_state") or {})
        state = dict(player.get("state") or {})
        stats = dict(state.get("stats") or {})
        for key, value in choice["effects"].items():
            if key in stats:
                stats[key] = int(stats.get(key, 0)) + int(value)
        state["stats"] = stats
        state["turns"] = int(state.get("turns") or 0) + 1
        state["energy"] = max(0, int(state.get("energy", 100)) - 4)
        state["health"] = max(0, min(100, int(state.get("health", 100)) + (2 if "Protect" in choice["label"] else 0)))
        rel = dict(state.get("relationships") or {})
        rel[choice["npc_id"]] = int(rel.get(choice["npc_id"], 0)) + int(choice["effects"].get("trust", 0))
        state["relationships"] = rel
        state["last_choice"] = choice_id
        state.setdefault("path", {})["chapter"] = int(state.get("turns", 0)) // 5
        state["path"]["branch"] = self.h(game["seed"], user_id, state["turns"], choice_id) % 12

        world["global_events"] = int(world.get("global_events") or 0) + 1
        world["arc"] = int(world.get("arc") or 0) + 1
        world["threat"] = max(0, min(10, int(world.get("threat") or 0) + int(choice.get("risk") or 0)))
        situation_key = f"{scene['id']}:{choice_id}"
        used = list(world.get("used_situations") or [])
        if situation_key not in used:
            used.append(situation_key)
        world["used_situations"] = used[-80:]

        # Every few actions the shared world creates a convergence point.
        if state["turns"] % CONVERGENCE_EVERY == 0:
            world["convergence"] = int(world.get("convergence") or 0) + 1
            world["shared_node"] = f"convergence_{world['convergence']}"
            loc = self._next_location(int(game["seed"]), self.h(game["seed"], world["convergence"], user_id), world.get("dimension"))
            world["location_id"], world["location_name"], world["dimension"] = loc
            world["dimension_name"] = self.dimension_name(loc[2])
            if loc[2] not in (world.get("discovered_dimensions") or []):
                world.setdefault("discovered_dimensions", []).append(loc[2])
            if loc[0] not in (world.get("discovered_locations") or []):
                world.setdefault("discovered_locations", []).append(loc[0])

        # Death is meaningful but never ends the shared world. The old path is discarded.
        danger = int(world.get("threat") or 0)
        died = int(self.h(game["seed"], user_id, state["turns"], choice_id, "death")) % 100 < min(5 + max(0, danger) * 2, 24) and int(choice.get("risk") or 0) > 0
        event: dict[str, Any] = {
            "type": "choice",
            "user_id": str(user_id),
            "choice_id": choice_id,
            "scene_id": scene["id"],
            "died": died,
            "turn": state["turns"],
        }
        if died:
            # Once a run dies we deliberately do not retain its old path, inventory,
            # relationships or scene transcript. Only the minimal death marker remains.
            state["deaths"] = int(state.get("deaths") or 0) + 1
            state["lives"] = int(state.get("lives") or 3) - 1
            if state["lives"] <= 0:
                state["cycle"] = int(state.get("cycle") or 0) + 1
                state["lives"] = 3
                state["flags"] = {"last_run_lost": True}
            # Do not keep the dead run's inventory/relationship/path transcript.
            respawn = self._next_location(int(game["seed"]), self.h(game["seed"], user_id, state["deaths"], state["cycle"], "respawn"), world.get("dimension"))
            state = {
                **self.initial_player_state(user_id),
                "deaths": int(state.get("deaths") or 0),
                "cycle": int(state.get("cycle") or 0),
                "lives": int(state.get("lives") or 3),
                "flags": {"last_run_lost": True},
                "checkpoint": {"location_id": respawn[0], "dimension": respawn[2]},
            }
            world["location_id"], world["location_name"], world["dimension"] = respawn
            world["dimension_name"] = self.dimension_name(respawn[2])
            event = {
                "type": "death",
                "user_id": str(user_id),
                "deaths": int(state.get("deaths") or 0),
                "lives": int(state.get("lives") or 3),
                "cycle": int(state.get("cycle") or 0),
            }

        result = await self.db.rpc("commit_world_turn", {
            "p_game_id": game_id,
            "p_player_id": player["id"],
            "p_expected_game_version": version,
            "p_expected_player_updated_at": player["updated_at"],
            "p_world_state": world,
            "p_player_state": state,
            "p_event": event,
        })
        if not result:
            raise DatabaseConflict("The world changed. Please try again.")
        return {"snapshot": await self.snapshot(game_id, user_id), "event": event}

    async def event(self, game_id: str, user_id: int | None, event_type: str, payload: dict[str, Any]) -> None:
        await self.db.request(
            "POST", "world_events",
            json={"game_id": game_id, "actor_user_id": user_id, "event_type": event_type, "payload": payload},
            prefer="return=minimal",
        )
