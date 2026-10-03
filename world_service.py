from __future__ import annotations

import asyncio
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from database import Database, DatabaseConflict, DatabaseError

# Procedural content is intentionally compact. A single seed creates a huge
# number of combinations without storing generated stories or calling Gemini.
DIMENSIONS = [
    ("ordinary", "The Living City"),
    ("coast", "The Salt Coast"),
    ("underworld", "The Underline"),
    ("mirror", "The Mirror District"),
    ("dream", "The Dreaming Quarter"),
    ("digital", "The Glass Network"),
    ("frontier", "The Far Frontier"),
    ("wild", "The Wild Country"),
    ("sky", "The High Expanse"),
    ("archive", "The Memory Vault"),
    ("deep", "The Deep Roads"),
    ("horizon", "The Last Horizon"),
]

LOCATIONS = [
    ("market", "Old Market", "ordinary"),
    ("station", "Central Station", "ordinary"),
    ("rooftops", "North Rooftops", "ordinary"),
    ("harbor", "Night Harbor", "coast"),
    ("lighthouse", "The Broken Lighthouse", "coast"),
    ("tunnels", "Service Tunnels", "underworld"),
    ("archive", "The Buried Archive", "underworld"),
    ("mirror_square", "Mirror Square", "mirror"),
    ("glass_house", "The Glass House", "mirror"),
    ("sleeping_hotel", "The Sleeping Hotel", "dream"),
    ("red_road", "The Red Road", "frontier"),
    ("orchard", "The Silent Orchard", "wild"),
    ("relay", "Relay Nine", "digital"),
    ("server_garden", "The Server Garden", "digital"),
    ("sky_dock", "Sky Dock Seven", "sky"),
    ("observatory", "The Empty Observatory", "sky"),
    ("memory_hall", "Memory Hall", "archive"),
    ("sealed_library", "The Sealed Library", "archive"),
    ("deep_bridge", "The Deep Bridge", "deep"),
    ("black_well", "The Black Well", "deep"),
    ("horizon_gate", "Horizon Gate", "horizon"),
    ("last_town", "The Last Town", "horizon"),
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
    ("vale", "Vale Mercer", "quiet negotiator"),
    ("theo", "Theo March", "friendly smuggler"),
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
    ("Another player has clearly changed this place", "multiplayer"),
    ("A stranger offers you a map with one location missing", "exploration"),
    ("A valuable item is being auctioned to people who should not exist", "faction"),
    ("A friend asks whether you remember a different version of them", "relationship"),
    ("A local festival turns into a negotiation for control of the district", "politics"),
    ("Someone offers to erase one mistake from your past", "sacrifice"),
    ("A machine predicts a choice you have not made yet", "technology"),
    ("You find a room containing objects from your possible futures", "dream"),
    ("A road appears behind you after you swear not to return", "exploration"),
    ("A faction claims your last choice changed their history", "consequence"),
    ("A wounded rival asks you for one honest answer", "relationship"),
    ("A market stall sells memories instead of goods", "wonder"),
    ("The sky briefly shows another dimension", "dimension"),
    ("A quiet stranger knows the name of someone you lost", "mystery"),
    ("A settlement asks you to choose who gets its final resource", "community"),
    ("Someone has been leaving gifts at your checkpoint", "relationship"),
    ("A dangerous shortcut promises to save days of travel", "risk"),
    ("The world offers you a position of influence", "politics"),
    ("A sealed elevator opens to a place that should be impossible", "dimension"),
    ("Two players arrive carrying different versions of the same object", "multiplayer"),
]

OPENERS = [
    "Nobody looks surprised, which is the first warning sign.",
    "You arrive a moment before the important part begins.",
    "The silence feels deliberate.",
    "One detail refuses to fit the story you were given.",
    "For once, the safest-looking option is also the strangest.",
    "You have the uncomfortable feeling that this moment happened before.",
    "Someone nearby is pretending not to watch you.",
    "The world seems ordinary until one impossible detail catches your eye.",
    "A choice is already waiting before anyone admits there is a choice.",
    "Several possible futures seem to narrow around this moment.",
    "The local story sounds simple. The evidence does not.",
    "Nobody asks you to get involved. That is exactly why you worry.",
    "The room feels familiar for a reason you cannot remember.",
    "You notice the exit first, then realize there are two more.",
    "A stranger smiles as if you have already agreed.",
    "The next minute feels more important than the last year.",
    "Someone has prepared this place for a visitor. You are not sure it was you.",
    "A harmless sound repeats until it becomes a pattern.",
    "The world gives you one quiet second to decide who you want to be.",
    "You can leave. That is what makes staying tempting.",
]

TWISTS = [
    "A second clue points somewhere completely different.",
    "The person who should be afraid is oddly calm.",
    "A familiar symbol appears where it should not exist.",
    "Someone offers help without asking for anything in return.",
    "The safest explanation turns out to be the least useful one.",
    "A distant sound makes everyone pause at the same time.",
    "One object seems to remember being somewhere else.",
    "The boundary between places flickers for a heartbeat.",
    "A stranger quietly uses your name even though you never introduced yourself.",
    "A promise made here may matter much later.",
    "You spot another player's influence in the aftermath of an earlier decision.",
    "There is a route forward, but it costs you something you may want later.",
    "Someone notices your hesitation and changes their offer.",
    "A door closes somewhere far away, and the whole district reacts.",
    "You realize someone has been protecting you without asking permission.",
    "The obvious villain may be the only person telling the truth.",
    "A relationship shifts because of something you did several scenes ago.",
    "The world remembers a promise you thought was private.",
    "A new route appears only because another player chose differently.",
    "The consequences are larger than the decision looked.",
]

CHOICES = [
    ("Approach carefully", {"courage": 1, "risk": 1}, "You move closer without revealing what you know."),
    ("Listen before acting", {"insight": 1, "trust": 1}, "You wait long enough to hear what others missed."),
    ("Take the opportunity", {"luck": 1, "risk": 2}, "You gamble on the opening before it disappears."),
    ("Protect the person involved", {"empathy": 1, "trust": 2}, "You put another person's safety ahead of your advantage."),
    ("Walk away and observe", {"insight": 2, "risk": -1}, "You give the situation room to reveal itself."),
    ("Tell the truth", {"honesty": 2, "trust": 1}, "You say the part that would have been easier to hide."),
    ("Keep the secret", {"cunning": 1, "trust": -1}, "You keep the information close and accept the cost."),
    ("Make a deal", {"cunning": 1, "luck": 1, "risk": 1}, "You trade something useful for a chance to move forward."),
    ("Search for another route", {"insight": 1, "luck": 1}, "You refuse the obvious path and look for a third option."),
    ("Trust your instinct", {"courage": 1, "empathy": 1, "risk": 1}, "You act before certainty arrives."),
    ("Ask for help", {"empathy": 1, "trust": 2}, "You let someone else become part of the decision."),
    ("Break the rule", {"courage": 2, "risk": 3}, "You deliberately cross a line that was supposed to stay untouched."),
    ("Offer a compromise", {"honesty": 1, "trust": 2, "risk": -1}, "You try to keep both sides in the room."),
    ("Investigate quietly", {"insight": 2, "cunning": 1}, "You gather information before choosing a side."),
    ("Commit publicly", {"courage": 2, "trust": 1, "risk": 2}, "You make your position impossible to misunderstand."),
    ("Protect your secret", {"cunning": 2, "trust": -2}, "You keep control of information even if someone is hurt by it."),
    ("Follow the impossible path", {"luck": 2, "risk": 2}, "You choose the route that should not exist."),
    ("Stay with them", {"empathy": 2, "trust": 2, "risk": 1}, "You refuse to leave someone alone with the consequences."),
]

CONVERGENCE_EVERY = 7


class WorldService:
    def __init__(self, db: Database):
        self.db = db
        self._locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def iso(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat()

    @staticmethod
    def h(*parts: Any) -> int:
        return int.from_bytes(hashlib.sha256("|".join(map(str, parts)).encode()).digest()[:8], "big")

    @classmethod
    def pick(cls, values: list[Any], key: int) -> Any:
        return values[key % len(values)]

    @classmethod
    def dimension_name(cls, ident: str) -> str:
        return next((name for key, name in DIMENSIONS if key == ident), "The Unknown")

    @classmethod
    def location(cls, ident: str) -> tuple[str, str, str]:
        return next((item for item in LOCATIONS if item[0] == ident), LOCATIONS[0])

    @classmethod
    def initial_player_state(cls, user_id: int) -> dict[str, Any]:
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
            "global_turn": 0,
            "arc": 0,
            "threat": 0,
            "convergence": 0,
            "convergence_active": False,
            "convergence_actions": 0,
            "location_id": loc[0],
            "location_name": loc[1],
            "dimension": loc[2],
            "dimension_name": cls.dimension_name(loc[2]),
            "weather": cls.pick(["clear", "rain", "wind", "fog", "heat", "quiet", "electric"], seed // 7),
            "discovered_dimensions": [loc[2]],
            "discovered_locations": [loc[0]],
            "used_scenes": [],
            "flags": {},
            "shared_node": "intro",
        }

    def _lock(self, game_id: str) -> asyncio.Lock:
        return self._locks.setdefault(game_id, asyncio.Lock())

    async def get_world(self, game_id: str) -> dict[str, Any]:
        rows = await self.db.request("GET", "world_games", params={"id": f"eq.{game_id}", "limit": "1"})
        if not rows:
            raise ValueError("World not found.")
        return rows[0]

    async def get_group_world(self, chat_id: int) -> dict[str, Any] | None:
        rows = await self.db.request("GET", "world_games", params={
            "chat_id": f"eq.{chat_id}",
            "status": "in.(waiting,active,paused)",
            "order": "created_at.desc",
            "limit": "1",
        })
        return rows[0] if rows else None

    async def get_player(self, game_id: str, user_id: int) -> dict[str, Any] | None:
        rows = await self.db.request("GET", "world_players", params={
            "game_id": f"eq.{game_id}",
            "telegram_user_id": f"eq.{user_id}",
            "limit": "1",
        })
        return rows[0] if rows else None

    async def players(self, game_id: str) -> list[dict[str, Any]]:
        return await self.db.request("GET", "world_players", params={
            "game_id": f"eq.{game_id}",
            "order": "joined_at.asc",
            "limit": "50",
        })

    async def create_world(self, creator_id: int, chat_id: int | None, title: str | None = None) -> dict[str, Any]:
        if chat_id is not None:
            existing = await self.get_group_world(chat_id)
            if existing:
                return existing
        seed = secrets.randbits(62)
        deadline = self.now() + timedelta(seconds=45)
        clean_title = (title or "WHAT HAPPENS?").strip()[:80] or "WHAT HAPPENS?"
        rows = await self.db.request("POST", "world_games", params={"select": "*"}, json={
            "creator_id": creator_id,
            "chat_id": chat_id,
            "title": clean_title,
            "status": "waiting",
            "join_deadline": self.iso(deadline),
            "started_at": None,
            "seed": seed,
            "world_state": self.initial_world_state(seed),
            "settings": {
                "allow_external_invites": True,
                "operator_ids": [],
                "max_players": 20,
            },
            "last_event": {"type": "world_created"},
            "version": 1,
            "max_players": 20,
        }, prefer="return=representation")
        if not rows:
            raise DatabaseError("Could not create world.")
        return rows[0]

    async def ensure_world_for_group(self, chat_id: int, creator_id: int) -> dict[str, Any]:
        # The partial unique index protects the final race. If two /play commands
        # arrive together, the second request simply re-reads the existing world.
        existing = await self.get_group_world(chat_id)
        if existing:
            return existing
        try:
            return await self.create_world(creator_id, chat_id)
        except Exception:
            existing = await self.get_group_world(chat_id)
            if existing:
                return existing
            raise

    async def ensure_active(self, game: dict[str, Any]) -> dict[str, Any]:
        if game.get("status") != "waiting":
            return game
        players = await self.players(str(game["id"]))
        if not players:
            # Keep the world forever. A future /play reopens the 45-second lobby.
            deadline = game.get("join_deadline")
            if deadline:
                dt = datetime.fromisoformat(str(deadline).replace("Z", "+00:00"))
                if self.now() >= dt:
                    rows = await self.db.request("PATCH", "world_games", params={"id": f"eq.{game['id']}", "status": "eq.waiting"}, json={
                        "join_deadline": self.iso(self.now() + timedelta(seconds=45)),
                        "version": int(game.get("version") or 1) + 1,
                        "last_event": {"type": "lobby_reopened"},
                    }, prefer="return=representation")
                    return rows[0] if rows else await self.get_world(str(game["id"]))
            return game
        deadline = game.get("join_deadline")
        if not deadline:
            return game
        dt = datetime.fromisoformat(str(deadline).replace("Z", "+00:00"))
        if self.now() < dt:
            return game
        rows = await self.db.request("PATCH", "world_games", params={"id": f"eq.{game['id']}", "status": "eq.waiting"}, json={
            "status": "active",
            "started_at": game.get("started_at") or self.iso(self.now()),
            "version": int(game.get("version") or 1) + 1,
            "last_event": {"type": "world_started"},
        }, prefer="return=representation")
        return rows[0] if rows else await self.get_world(str(game["id"]))

    async def join(self, game_id: str, user_id: int, username: str, display_name: str) -> dict[str, Any]:
        game = await self.ensure_active(await self.get_world(game_id))
        if game.get("status") == "paused":
            raise ValueError("This world is paused by its game moderators.")
        if game.get("status") == "archived":
            raise ValueError("This world has ended.")
        existing = await self.get_player(game_id, user_id)
        if existing:
            return existing
        result = await self.db.rpc("join_world_atomic", {
            "p_game_id": game_id,
            "p_user_id": user_id,
            "p_username": username or "",
            "p_display_name": display_name or "Player",
        })
        if isinstance(result, list):
            return result[0]
        return result

    def _next_location(self, seed: int, key: int, current_dimension: str | None) -> tuple[str, str, str]:
        # Usually move to a different dimension. Occasionally stay put for continuity.
        candidates = [x for x in LOCATIONS if x[2] != current_dimension] or LOCATIONS
        return self.pick(candidates, key)

    def _scene_for(self, game: dict[str, Any], player: dict[str, Any]) -> dict[str, Any]:
        world = game.get("world_state") or {}
        state = player.get("state") or {}
        seed = int(world.get("seed") or game.get("seed") or 1)
        turn = int(state.get("turns") or 0)
        cycle = int(state.get("cycle") or 0)
        uid = int(player["telegram_user_id"])
        shared_node = str(world.get("shared_node") or "intro")
        loc = self.location(str(world.get("location_id") or "market"))

        # During a convergence, everyone sees the same scene and choices.
        if world.get("convergence_active"):
            npc = self.pick(NPCS, self.h(seed, world.get("convergence", 0), "conv-npc"))
            title = f"{loc[1]} — The Meeting Point"
            text = (
                f"Paths that began separately arrive at {loc[1]}. {npc[1]}, {npc[2]}, "
                "is waiting beside a table covered in objects that different players remember differently. "
                "For a moment, every choice belongs to everyone."
            )
            category = "convergence"
            base = self.h(seed, world.get("convergence", 0), "convergence-choices")
        else:
            npc = self.pick(NPCS, self.h(seed, turn, cycle, uid, "npc", shared_node, loc[0]))
            situation = self.pick(SITUATIONS, self.h(seed, turn, cycle, uid, "situation", shared_node, loc[0]))
            used = set(world.get("used_scenes") or [])
            key = f"{cycle}:{turn}:{loc[0]}:{situation[0]}"
            if key in used:
                for offset in range(1, len(SITUATIONS)):
                    candidate = SITUATIONS[(self.h(seed, turn, cycle, uid, "alt") + offset) % len(SITUATIONS)]
                    candidate_key = f"{cycle}:{turn}:{loc[0]}:{candidate[0]}"
                    if candidate_key not in used:
                        situation = candidate
                        key = candidate_key
                        break
            opener = self.pick(OPENERS, self.h(seed, turn, cycle, uid, "opener", loc[0]))
            twist = self.pick(TWISTS, self.h(seed, turn, cycle, uid, "twist", loc[0]))
            bond = int((state.get("relationships") or {}).get(npc[0], 0))
            text = f"{opener} In {loc[1]}, {situation[0].lower()}. {npc[1]} is nearby, a {npc[2]}. {twist}"
            if bond >= 4:
                text += f" {npc[1]} trusts you enough to wait for your answer."
            elif bond <= -4:
                text += f" {npc[1]} watches you as if expecting betrayal."
            title = f"{loc[1]} — {situation[0]}"
            category = situation[1]
            base = self.h(seed, turn, cycle, uid, shared_node, loc[0], situation[1])

        choices: list[dict[str, Any]] = []
        for i in range(3):
            label, effects, consequence = CHOICES[(base + i * 5) % len(CHOICES)]
            choices.append({
                "id": f"c{i + 1}",
                "label": label,
                "preview": consequence,
                "risk": max(-1, min(4, int(effects.get("risk", 0)))),
                "effects": effects,
                "npc_id": npc[0],
            })
        scene_id = f"{world.get('arc', 0)}:{cycle}:{turn}:{shared_node}:{loc[0]}:{category}:{base % 10000}"
        return {
            "id": scene_id,
            "title": title,
            "text": text,
            "category": category,
            "location": loc[1],
            "dimension": self.dimension_name(loc[2]),
            "npc": {"id": npc[0], "name": npc[1], "bond": int((state.get("relationships") or {}).get(npc[0], 0))},
            "convergence": bool(world.get("convergence_active")),
            "choices": choices,
        }

    def present(self, game: dict[str, Any], player: dict[str, Any] | None, roster: list[dict[str, Any]]) -> dict[str, Any]:
        world = game.get("world_state") or {}
        state = (player or {}).get("state") or {}
        return {
            "game": {
                "id": str(game["id"]),
                "title": game.get("title") or "WHAT HAPPENS?",
                "status": game.get("status"),
                "version": int(game.get("version") or 1),
                "max_players": int(game.get("max_players") or 20),
                "join_deadline": game.get("join_deadline"),
                "started_at": game.get("started_at"),
                "chat_id": game.get("chat_id"),
                "creator_id": game.get("creator_id"),
                "settings": game.get("settings") or {"allow_external_invites": True, "operator_ids": []},
            },
            "player": None if not player else {
                "id": str(player["id"]),
                "display_name": player.get("display_name") or "Player",
                "username": player.get("username") or "",
                "status": player.get("status") or "alive",
                "version": int(player.get("version") or 1),
                "state": state,
            },
            "players": [
                {"id": str(p["id"]), "display_name": p.get("display_name") or "Player", "username": p.get("username") or "", "status": p.get("status") or "alive", "joined_at": p.get("joined_at")}
                for p in roster
            ],
            "world": {
                "location": world.get("location_name") or "Unknown",
                "dimension": world.get("dimension_name") or "The Unknown",
                "turn": int(world.get("global_turn") or 0),
                "convergence": int(world.get("convergence") or 0),
                "convergence_active": bool(world.get("convergence_active")),
                "threat": int(world.get("threat") or 0),
                "discoveries": list(world.get("discovered_dimensions") or [])[-8:],
                "locations": list(world.get("discovered_locations") or [])[-8:],
            },
            "scene": self._scene_for(game, player) if player and game.get("status") == "active" and state.get("status") == "alive" else None,
        }

    async def snapshot(self, game_id: str, user_id: int, include_unjoined: bool = True) -> dict[str, Any]:
        game = await self.ensure_active(await self.get_world(game_id))
        player = await self.get_player(game_id, user_id)
        if not player and not include_unjoined:
            raise ValueError("Join this world first.")
        return self.present(game, player, await self.players(game_id))

    async def choose(self, game_id: str, user_id: int, choice_id: str) -> dict[str, Any]:
        lock = self._lock(game_id)
        async with lock:
            game = await self.ensure_active(await self.get_world(game_id))
            if game.get("status") != "active":
                raise ValueError("This world is not currently accepting story choices.")
            player = await self.get_player(game_id, user_id)
            if not player:
                raise ValueError("Join the world first.")
            state = dict(player.get("state") or {})
            if state.get("status") != "alive":
                raise ValueError("Your current run has ended. Re-enter the world to begin a new path.")
            scene = self._scene_for(game, player)
            choice = next((c for c in scene["choices"] if c["id"] == choice_id), None)
            if not choice:
                raise ValueError("That choice is no longer available. Refreshing the story will give you the current choice.")

            world = dict(game.get("world_state") or {})
            stats = dict(state.get("stats") or {})
            for key, value in choice["effects"].items():
                if key in stats:
                    stats[key] = int(stats.get(key, 0)) + int(value)
            state["stats"] = stats
            state["turns"] = int(state.get("turns") or 0) + 1
            state["energy"] = max(0, int(state.get("energy", 100)) - 3)
            state["health"] = max(0, min(100, int(state.get("health", 100)) + (3 if choice["effects"].get("empathy") else 0)))
            rel = dict(state.get("relationships") or {})
            npc_id = str(choice.get("npc_id") or "")
            if npc_id:
                rel[npc_id] = int(rel.get(npc_id, 0)) + int(choice["effects"].get("trust", 0))
            state["relationships"] = rel
            state["last_choice"] = choice_id
            state.setdefault("path", {})["chapter"] = int(state["turns"]) // 6
            state["path"]["branch"] = self.h(game["seed"], user_id, state["turns"], choice_id) % 64
            state["path"]["node"] = f"{world.get('arc', 0)}:{state['path']['branch']}"

            world["global_turn"] = int(world.get("global_turn") or 0) + 1
            world["arc"] = int(world.get("arc") or 0) + 1
            world["threat"] = max(0, min(12, int(world.get("threat") or 0) + int(choice["effects"].get("risk", 0))))
            used = list(world.get("used_scenes") or [])
            used.append(scene["id"])
            world["used_scenes"] = used[-120:]

            convergence_started = False
            if not world.get("convergence_active") and int(world["global_turn"]) % CONVERGENCE_EVERY == 0:
                world["convergence"] = int(world.get("convergence") or 0) + 1
                world["convergence_active"] = True
                world["convergence_actions"] = 0
                world["shared_node"] = f"convergence_{world['convergence']}"
                loc = self._next_location(int(game["seed"]), self.h(game["seed"], world["convergence"], "convergence"), world.get("dimension"))
                world["location_id"], world["location_name"], world["dimension"] = loc
                world["dimension_name"] = self.dimension_name(loc[2])
                if loc[2] not in world.get("discovered_dimensions", []):
                    world.setdefault("discovered_dimensions", []).append(loc[2])
                if loc[0] not in world.get("discovered_locations", []):
                    world.setdefault("discovered_locations", []).append(loc[0])
                convergence_started = True

            if world.get("convergence_active"):
                world["convergence_actions"] = int(world.get("convergence_actions") or 0) + 1
                alive_count = max(1, len([p for p in await self.players(game_id) if p.get("status") == "alive"]))
                if int(world["convergence_actions"]) >= min(3, max(2, alive_count)):
                    world["convergence_active"] = False
                    world["shared_node"] = f"branch_{world['global_turn']}"

            danger = int(world.get("threat") or 0)
            risk = int(choice["effects"].get("risk", 0))
            death_chance = min(28, max(0, 4 + danger * 2 + risk * 3))
            died = risk > 0 and self.h(game["seed"], user_id, state["turns"], choice_id, "death") % 100 < death_chance
            event: dict[str, Any] = {
                "type": "choice",
                "user_id": str(user_id),
                "choice_id": choice_id,
                "scene_id": scene["id"],
                "turn": state["turns"],
                "convergence": bool(convergence_started),
                "died": bool(died),
            }

            if died:
                deaths = int(state.get("deaths") or 0) + 1
                lives = int(state.get("lives") or 3) - 1
                cycle = int(state.get("cycle") or 0)
                if lives <= 0:
                    cycle += 1
                    lives = 3
                respawn = self._next_location(int(game["seed"]), self.h(game["seed"], user_id, deaths, cycle, "respawn"), world.get("dimension"))
                # Do not preserve the dead run. Only compact survival metadata survives.
                state = self.initial_player_state(user_id)
                state.update({
                    "deaths": deaths,
                    "cycle": cycle,
                    "lives": lives,
                    "flags": {"last_run_lost": True},
                    "checkpoint": {"location_id": respawn[0], "dimension": respawn[2]},
                })
                world["location_id"], world["location_name"], world["dimension"] = respawn
                world["dimension_name"] = self.dimension_name(respawn[2])
                event.update({"type": "death", "deaths": deaths, "lives": lives, "cycle": cycle, "respawn": {"location": respawn[1], "dimension": self.dimension_name(respawn[2])}})

            result = await self.db.rpc("commit_world_turn", {
                "p_game_id": game_id,
                "p_player_id": player["id"],
                "p_expected_game_version": int(game.get("version") or 1),
                "p_expected_player_version": int(player.get("version") or 1),
                "p_world_state": world,
                "p_player_state": state,
                "p_event": event,
            })
            if not result:
                raise DatabaseConflict("The world changed. Please try again.")
            return {"snapshot": await self.snapshot(game_id, user_id), "event": event}

    async def create_invite(self, game_id: str, user_id: int) -> str:
        token = secrets.token_urlsafe(12).replace("-", "_").replace("=", "")[:24]
        rows = await self.db.request("POST", "world_invites", params={"select": "token"}, json={
            "token": token, "game_id": game_id, "created_by": user_id, "uses": 0, "max_uses": 100, "active": True,
        }, prefer="return=representation")
        return str(rows[0]["token"] if rows else token)

    async def consume_invite(self, token: str, user_id: int, username: str, name: str) -> dict[str, Any]:
        rows = await self.db.request("GET", "world_invites", params={"token": f"eq.{token}", "active": "eq.true", "limit": "1"})
        if not rows:
            raise ValueError("Invite link is invalid or expired.")
        invite = rows[0]
        if int(invite.get("uses") or 0) >= int(invite.get("max_uses") or 100):
            raise ValueError("This invite has reached its limit.")
        player = await self.join(str(invite["game_id"]), user_id, username, name)
        await self.db.request("PATCH", "world_invites", params={"token": f"eq.{token}"}, json={"uses": int(invite.get("uses") or 0) + 1}, prefer="return=minimal")
        return {"game": await self.get_world(str(invite["game_id"])), "player": player}

    async def update_settings(self, game_id: str, values: dict[str, Any]) -> dict[str, Any]:
        rows = await self.db.request("PATCH", "world_games", params={"id": f"eq.{game_id}"}, json=values, prefer="return=representation")
        if not rows:
            raise ValueError("World not found.")
        return rows[0]

    async def pause(self, game_id: str, actor_id: int) -> dict[str, Any]:
        return await self.update_settings(game_id, {"status": "paused", "last_event": {"type": "paused", "actor": actor_id}})

    async def resume(self, game_id: str, actor_id: int) -> dict[str, Any]:
        return await self.update_settings(game_id, {"status": "active", "last_event": {"type": "resumed", "actor": actor_id}})

    async def terminate(self, game_id: str, actor_id: int) -> dict[str, Any]:
        return await self.update_settings(game_id, {"status": "archived", "last_event": {"type": "terminated", "actor": actor_id}})

    async def set_operators(self, game_id: str, operator_ids: list[int]) -> dict[str, Any]:
        game = await self.get_world(game_id)
        settings = dict(game.get("settings") or {})
        settings["operator_ids"] = sorted({int(x) for x in operator_ids})[:10]
        return await self.update_settings(game_id, {"settings": settings})

    async def maintenance(self) -> dict[str, Any] | None:
        rows = await self.db.request("GET", "world_control", params={"id": "eq.1", "limit": "1"})
        return rows[0] if rows else None

    async def maintenance_active(self) -> bool:
        row = await self.maintenance()
        if not row or not row.get("maintenance_until"):
            return False
        try:
            until = datetime.fromisoformat(str(row["maintenance_until"]).replace("Z", "+00:00"))
            return self.now() < until
        except Exception:
            return False
