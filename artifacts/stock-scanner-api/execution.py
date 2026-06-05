POSITIONS: dict = {}
CASH: float = 100_000.0
STARTING_CASH: float = 100_000.0


def enter_trade(ticker: str, price: float, size: int = 10) -> bool:
    global CASH
    cost = price * size
    if CASH < cost:
        return False
    CASH -= cost
    POSITIONS[ticker] = {"entry": price, "size": size}
    return True


def exit_trade(ticker: str, price: float):
    global CASH
    if ticker not in POSITIONS:
        return None
    position = POSITIONS[ticker]
    pnl = (price - position["entry"]) * position["size"]
    CASH += price * position["size"]
    del POSITIONS[ticker]
    return pnl


def get_positions() -> dict:
    return dict(POSITIONS)


def get_cash() -> float:
    return CASH


def reset():
    global POSITIONS, CASH
    POSITIONS = {}
    CASH = STARTING_CASH
