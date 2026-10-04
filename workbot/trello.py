"""Minimal async client for the Trello REST API (https://developer.atlassian.com/cloud/trello/rest/)."""

from typing import Any

import aiohttp

API = "https://api.trello.com/1"

CARD_FIELDS = "name,desc,idShort,idList,idLabels,idMembers,due,dueComplete,start,shortLink,shortUrl,closed,badges"


class TrelloError(Exception):
    def __init__(self, status: int, text: str):
        super().__init__(f"Trello {status}: {text[:300]}")
        self.status = status


class Trello:
    def __init__(self, key: str, token: str):
        self.key, self.token = key, token

    @property
    def configured(self) -> bool:
        return bool(self.key and self.token)

    async def call(self, method: str, path: str, *, params: dict | None = None, json: dict | None = None) -> Any:
        # Credentials go in the header, so they never appear in logged URLs.
        headers = {"Authorization": f'OAuth oauth_consumer_key="{self.key}", oauth_token="{self.token}"'}
        query = {k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in (params or {}).items()}
        # A session per call: serverless invocations may each run on a new event loop.
        async with aiohttp.ClientSession(trust_env=True, timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.request(method, API + path, params=query, json=json, headers=headers) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    raise TrelloError(resp.status, text)
                return await resp.json(content_type=None) if text else None

    # -- members ------------------------------------------------------------

    async def me(self) -> dict:
        return await self.call("GET", "/members/me", params={"fields": "id,username,fullName"})

    async def member(self, username: str) -> dict:
        return await self.call("GET", f"/members/{username}", params={"fields": "id,username,fullName"})

    # -- board --------------------------------------------------------------

    async def create_board(self, name: str, workspace: str = "") -> dict:
        body: dict[str, Any] = {
            "name": name, "defaultLists": False, "defaultLabels": False,
            "prefs_permissionLevel": "org" if workspace else "private",
        }
        if workspace:
            body["idOrganization"] = workspace
        return await self.call("POST", "/boards", json=body)

    async def create_list(self, board_id: str, name: str, pos: int) -> dict:
        return await self.call("POST", "/lists", json={"idBoard": board_id, "name": name, "pos": pos})

    async def create_label(self, board_id: str, name: str, color: str | None) -> dict:
        return await self.call("POST", f"/boards/{board_id}/labels", json={"name": name, "color": color})

    async def board_members(self, board_id: str) -> list[dict]:
        return await self.call("GET", f"/boards/{board_id}/members", params={"fields": "id,username,fullName"})

    async def board_add_member(self, board_id: str, member_id: str) -> None:
        await self.call("PUT", f"/boards/{board_id}/members/{member_id}", json={"type": "normal"})

    async def board_invite_email(self, board_id: str, email: str) -> None:
        await self.call("PUT", f"/boards/{board_id}/members", json={"email": email, "type": "normal"})

    async def board_cards(self, board_id: str) -> list[dict]:
        return await self.call("GET", f"/boards/{board_id}/cards/open", params={"fields": CARD_FIELDS})

    async def board_card(self, board_id: str, number: int) -> dict:
        """A card by its number on the board (idShort, the #42 shown in Trello)."""
        return await self.call("GET", f"/boards/{board_id}/cards/{number}", params={"fields": "id"})

    # -- cards --------------------------------------------------------------

    async def card(self, card_id: str) -> dict:
        return await self.call("GET", f"/cards/{card_id}", params={
            "fields": CARD_FIELDS, "members": True, "member_fields": "fullName,username",
        })

    async def create_card(self, **fields: Any) -> dict:
        return await self.call("POST", "/cards", json=fields)

    async def update_card(self, card_id: str, **fields: Any) -> dict:
        return await self.call("PUT", f"/cards/{card_id}", json=fields)

    async def delete_card(self, card_id: str) -> None:
        await self.call("DELETE", f"/cards/{card_id}")

    async def add_card_member(self, card_id: str, member_id: str) -> None:
        try:
            await self.call("POST", f"/cards/{card_id}/idMembers", json={"value": member_id})
        except TrelloError as exc:
            if "already" not in str(exc).lower():
                raise

    async def remove_card_member(self, card_id: str, member_id: str) -> None:
        await self.call("DELETE", f"/cards/{card_id}/idMembers/{member_id}")

    async def comment(self, card_id: str, text: str) -> None:
        await self.call("POST", f"/cards/{card_id}/actions/comments", json={"text": text})

    async def add_checklist(self, card_id: str, name: str, items: list[str]) -> None:
        checklist = await self.call("POST", f"/cards/{card_id}/checklists", json={"name": name})
        for item in items:
            await self.call("POST", f"/checklists/{checklist['id']}/checkItems", json={"name": item})
