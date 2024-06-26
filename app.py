import asyncio
import time
from typing import List, Tuple, Dict

from fastapi import FastAPI, WebSocket
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from starlette.requests import Request
from starlette.responses import RedirectResponse
from starlette.staticfiles import StaticFiles
import random
from enum import Enum
import logging

from starlette.websockets import WebSocketDisconnect

import config

app = FastAPI()
templates = Jinja2Templates(directory="html")
app.mount("/static", StaticFiles(directory="static"), name="static")


def get_username(request: Request):
    return request.cookies.get("username", None)


@app.get("/", response_class=RedirectResponse, status_code=302)
async def index(request: Request):
    username = get_username(request)
    if username:
        return RedirectResponse(url="/game")
    return RedirectResponse(url="/start")


@app.get("/game", response_class=HTMLResponse)
async def game(request: Request):
    username = get_username(request)
    return templates.TemplateResponse(
        "game.html",
        {
            "request": request,
            "username": username,
            "FONTAWESOME_URL": config.FONTAWESOME_URL,
        },
    )


@app.get("/start", response_class=HTMLResponse)
async def start(request: Request):
    return templates.TemplateResponse("start.html", {"request": request})


@app.get("/login", response_class=RedirectResponse, status_code=302)
async def login(request: Request):
    response = RedirectResponse(url="/game")
    response.set_cookie("username", request.query_params["username"])
    return response


@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login(request: Request):
    return templates.TemplateResponse("admin_login.html", {"request": request})


@app.post("/admin/login", response_class=RedirectResponse)
async def admin_login_post(request: Request):
    response = RedirectResponse(url="/admin", status_code=302)
    response.set_cookie("admin", (await request.form())["password"])
    return response


@app.get("/admin", response_class=HTMLResponse)
async def admin(request: Request):
    if request.cookies.get("admin") != config.ADMIN_PASSWORD:
        return RedirectResponse(url="/admin/login")
    return templates.TemplateResponse("admin.html", {"request": request})


@app.post("/admin/change_balance")
async def change_balance(request: Request):
    if request.cookies.get("admin") != config.ADMIN_PASSWORD:
        return RedirectResponse(url="/admin/login")
    data = await request.form()
    player = game.players.get(data["username"])
    if not player:
        return {"error": "Player not found"}
    old_balance = player.balance
    player.balance = int(data["balance"])
    actioned = player.balance - old_balance
    if old_balance > int(data["balance"]):
        action = "remove"
    else:
        action = "win"
    await player.send(
        {
            "type": "balance",
            "balance": player.balance,
            "action": action,
            "actioned": actioned,
        }
    )
    return {"message": "Balance updated"}


class Deck:
    def __init__(self):
        self.cards = [
            f"{j}_of_{i}"
            for i in ["hearts", "diamonds", "clubs", "spades"]
            for j in [
                "2",
                "3",
                "4",
                "5",
                "6",
                "7",
                "8",
                "9",
                "10",
                "jack",
                "queen",
                "king",
                "ace",
            ]
        ]
        print(f"Deck: {self.cards}")
        random.shuffle(self.cards)

    def draw(self):
        return self.cards.pop()

    def __len__(self):
        return len(self.cards)


class BetType(str, Enum):
    player = "player"
    banker = "banker"
    player_pair = "player_pair"
    banker_pair = "banker_pair"
    tie = "tie"


class Bet:
    def __init__(self, player, bet_type: BetType, bet: int):
        self.player = player
        self.bet_type = bet_type
        self.bet = bet

    def __str__(self):
        return f"{self.player.name} bet {self.bet} on {self.bet_type}"


class Player:
    def __init__(self, name, socket, on_disconnect):
        self.name = name
        self.socket = socket
        self.bet: Bet = None
        self.balance = 1000
        self.on_disconnect = on_disconnect

    async def send(self, message):
        try:
            await self.socket.send_json(message)
        except (WebSocketDisconnect, RuntimeError):
            await self.on_disconnect(self)

    async def place_bet(self, bet_type: BetType, bet: int):
        if bet > self.balance:
            await self.send({"type": "error", "message": "Insufficient balance"})
            return
        # if self.bet:
        #     await self.send({"type": "error", "message": "You already placed a bet"})
        #     return
        await self.remove_bet()
        self.bet = Bet(self, bet_type, bet)

    async def remove_bet(self):
        self.bet = None

    async def win(self, amount):
        self.balance += amount
        await self.send(
            {
                "type": "balance",
                "balance": self.balance,
                "actioned": amount,
                "action": "win",
            }
        )

    async def remove_balance(self, amount):
        self.balance -= amount
        await self.send(
            {
                "type": "balance",
                "balance": self.balance,
                "action": "remove",
                "actioned": amount,
            }
        )

    def __str__(self):
        return self.name


def score(cards):
    card_values = {
        "ace": 1,
        "2": 2,
        "3": 3,
        "4": 4,
        "5": 5,
        "6": 6,
        "7": 7,
        "8": 8,
        "9": 9,
        "10": 0,
        "jack": 0,
        "queen": 0,
        "king": 0,
    }
    s_ = 0
    for card in cards:
        value = card.split("_")[0]
        s_ += card_values.get(value, 0)
    return s_ % 10  # Ensure the score doesn't exceed 9


class Game:
    def __init__(self):
        self.history = []
        self.players: dict[str, Player] = {}
        self.deck = Deck()
        self.last_time = time.time()
        self.is_active = False

        self.banker_cards = []
        self.player_cards = []
        self.banker_score = 0
        self.player_score = 0

    async def start(self):
        while True:
            if time.time() - self.last_time > 10 and not self.is_active:
                await self.deal_valid()
                await asyncio.sleep(5)
                self.last_time = time.time()
                await self.broadcast({"type": "clear"})
            await asyncio.sleep(1)
            await self.broadcast(
                {"type": "time", "time": 10 - (time.time() - self.last_time)}
            )

    async def deal_valid(self):
        self.is_active = True
        await self.broadcast({"type": "start"})

        for player_name in list(self.players):
            player = self.players[player_name]
            if player.bet:
                await player.remove_balance(player.bet.bet)

        await asyncio.sleep(1)

        self.banker_cards = [self.deck.draw()]
        self.banker_score = score(self.banker_cards)
        await self.broadcast(
            {
                "type": "deal",
                "card": self.banker_cards[0],
                "deal_type": "banker",
                "score": self.banker_score,
                "index": 0,
            }
        )
        await asyncio.sleep(1)
        self.player_cards = [self.deck.draw()]
        self.player_score = score(self.player_cards)
        await self.broadcast(
            {
                "type": "deal",
                "card": self.player_cards[0],
                "deal_type": "player",
                "score": self.player_score,
                "index": 0,
            }
        )
        await asyncio.sleep(1)
        self.banker_cards.append(self.deck.draw())
        self.banker_score = score(self.banker_cards)
        await self.broadcast(
            {
                "type": "deal",
                "card": self.banker_cards[1],
                "deal_type": "banker",
                "score": self.banker_score,
                "index": 1,
            }
        )
        await asyncio.sleep(1)
        self.player_cards.append(self.deck.draw())
        self.player_score = score(self.player_cards)
        await self.broadcast(
            {
                "type": "deal",
                "card": self.player_cards[1],
                "deal_type": "player",
                "score": self.player_score,
                "index": 1,
            }
        )
        self.banker_score = score(self.banker_cards)
        self.player_score = score(self.player_cards)
        # take additional card if rules
        if self.player_score < 6:
            await asyncio.sleep(1)
            self.player_cards.append(self.deck.draw())
            self.player_score = score(self.player_cards)
            await self.broadcast(
                {
                    "type": "deal",
                    "card": self.player_cards[-1],
                    "deal_type": "player",
                    "score": self.player_score,
                    "index": 2,
                }
            )
        if self.banker_score < 6:
            await asyncio.sleep(1)
            self.banker_cards.append(self.deck.draw())
            self.banker_score = score(self.banker_cards)
            await self.broadcast(
                {
                    "type": "deal",
                    "card": self.banker_cards[-1],
                    "deal_type": "banker",
                    "score": self.banker_score,
                    "index": 2,
                }
            )
        # determine winner
        if (
            self.player_cards[0].split("_")[0] == self.player_cards[1].split("_")[0]
            and len(self.player_cards) == 2
        ):
            winner = "player_pair"
            x = 10.0
        elif (
            self.banker_cards[0].split("_")[0] == self.banker_cards[1].split("_")[0]
            and len(self.banker_cards) == 2
        ):
            winner = "banker_pair"
            x = 10.0
        elif self.player_score > self.banker_score:
            winner = "player"
            x = 2.0
        elif self.player_score < self.banker_score:
            winner = "banker"
            x = 1.9
        else:
            winner = "tie"
            x = 8.0
        self.history.append(
            {
                "banker": self.banker_cards,
                "player": self.player_cards,
                "winner": winner,
            }
        )
        # if history length > 10, pop the oldest
        if len(self.history) > 9:
            self.history.pop(0)

        await asyncio.sleep(0.1)
        await self.broadcast(
            {
                "type": "result",
                "banker": self.banker_cards,
                "player": self.player_cards,
                "winner": winner,
            }
        )

        # find winners
        for player_name in list(self.players):
            player = self.players[player_name]
            if player.bet:
                if player.bet.bet_type == winner:
                    await player.win(player.bet.bet * x)
                await player.remove_bet()

        self.deck = Deck()

        self.is_active = False

    async def add_player(self, name: str, player: Player):
        self.players[name] = player

    async def remove_player(self, player: Player):
        self.players.pop(player.name)

    async def broadcast(self, message):
        for player_name in list(self.players):
            player = self.players[player_name]
            try:
                await player.send(message)
            except WebSocketDisconnect:
                await self.remove_player(player)
            except RuntimeError:
                await self.remove_player(player)


game = Game()

logging.basicConfig(level=logging.DEBUG)


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(game.start())


@app.websocket("/game")
async def game_socket(websocket: WebSocket):
    await websocket.accept()

    player = None

    while True:
        try:
            data = await websocket.receive_json()
        except WebSocketDisconnect:
            break

        if data["type"] == "name":
            if data["name"] in game.players:
                player = game.players[data["name"]]
                player.socket = websocket
            else:
                player = Player(data["name"], websocket, game.remove_player)
                await game.add_player(data["name"], player)
            await player.send({"type": "history", "history": game.history})
            await player.send({"type": "balance", "balance": player.balance})
            if game.is_active:
                await player.send(
                    {
                        "type": "game",
                        "banker": {
                            "cards": game.banker_cards,
                            "score": game.banker_score,
                        },
                        "player": {
                            "cards": game.player_cards,
                            "score": game.player_score,
                        },
                    }
                )
        elif data["type"] == "bet":
            if game.is_active:
                await player.send(
                    {"type": "error", "message": "Game is already active"}
                )
                continue
            value = data["value"]
            amount = data["amount"]
            await player.place_bet(value, amount)
        elif data["type"] == "unbet":
            if game.is_active:
                await player.send(
                    {"type": "error", "message": "Game is already active"}
                )
                continue
            await player.remove_bet()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="localhost", port=80)
