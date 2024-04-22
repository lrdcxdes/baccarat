import asyncio
import time
from typing import List, Tuple

from fastapi import FastAPI, WebSocket
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from starlette.requests import Request
from starlette.staticfiles import StaticFiles
import random
from enum import Enum
import logging

from starlette.websockets import WebSocketDisconnect

app = FastAPI()
templates = Jinja2Templates(directory="html")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


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
    tie = "tie"


class Bet:
    def __init__(self, player, bet_type: BetType, bet: int):
        self.player = player
        self.bet_type = bet_type
        self.bet = bet

    def __str__(self):
        return f"{self.player.name} bet {self.bet} on {self.bet_type}"


class Player:
    def __init__(self, name, socket):
        self.name = name
        self.socket = socket
        self.bets: List[Bet] = []

    async def send(self, message):
        await self.socket.send_json(message)

    async def bet(self, bet_type: BetType, bet: int):
        self.bets.append(Bet(self, bet_type, bet))
        await self.send({"type": "bet", "bet": bet_type, "amount": bet})

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
        self.players: List[Player] = []
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
        # await asyncio.sleep(2)
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
        if self.player_score > self.banker_score:
            winner = "player"
        elif self.player_score < self.banker_score:
            winner = "banker"
        else:
            winner = "tie"
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
        self.deck = Deck()

        self.is_active = False

    async def add_player(self, player: Player):
        try:
            self.players.append(player)
            await player.send({"type": "history", "history": self.history})
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
        except WebSocketDisconnect:
            await self.remove_player(player)

    async def remove_player(self, player: Player):
        self.players.remove(player)

    async def broadcast(self, message):
        for player in self.players:
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
    while True:
        try:
            data = await websocket.receive_json()
        except WebSocketDisconnect:
            break
        if data["type"] == "name":
            player = Player(data["name"], websocket)
            await game.add_player(player)
        elif data["type"] == "bet":
            value = data["value"]
            amount = data["amount"]



if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="localhost", port=80)
