"""Módulo de configuración centralizada de TradIA.

Carga y valida los parámetros de config/config.yaml incluyendo
horarios de Madrid y modo simulación (paper trading).
"""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml


@dataclass
class NotificationsConfig:
    channel: str = "email"  # "email" o "whatsapp"
    heartbeat_emails: bool = True  # Confirmación corta en cada ciclo de 15m (las 24h)


@dataclass
class MarketConfig:
    exchange: str = "binance"
    symbols: List[str] = field(default_factory=lambda: ["BTC/USDT", "ETH/USDT"])
    timeframe: str = "15m"


@dataclass
class WindowConfig:
    start: str = "08:30"
    end: str = "17:30"


@dataclass
class ScheduleConfig:
    timezone: str = "Europe/Madrid"
    morning_window: WindowConfig = field(default_factory=lambda: WindowConfig("08:30", "17:30"))
    evening_window: WindowConfig = field(default_factory=lambda: WindowConfig("20:30", "23:00"))
    force_close_time: str = "22:45"
    daily_report_time: str = "23:00"
    extended_hours_until: Optional[str] = None


@dataclass
class RSIConfig:
    period: int = 14
    oversold: float = 30.0
    overbought: float = 70.0


@dataclass
class EMAConfig:
    fast_period: int = 9
    slow_period: int = 21


@dataclass
class MACDConfig:
    fast_period: int = 12
    slow_period: int = 26
    signal_period: int = 9


@dataclass
class BollingerConfig:
    period: int = 20
    std_dev: float = 2.0


@dataclass
class VolumeConfig:
    sma_period: int = 20
    multiplier_threshold: float = 1.5


@dataclass
class IndicatorsConfig:
    rsi: RSIConfig = field(default_factory=RSIConfig)
    ema: EMAConfig = field(default_factory=EMAConfig)
    macd: MACDConfig = field(default_factory=MACDConfig)
    bollinger: BollingerConfig = field(default_factory=BollingerConfig)
    volume: VolumeConfig = field(default_factory=VolumeConfig)


@dataclass
class StrategyConfig:
    mode: str = "scoring"
    min_score_buy: int = 3
    min_score_sell: int = 3
    weights: Dict[str, int] = field(
        default_factory=lambda: {
            "rsi": 2,
            "ema_cross": 2,
            "macd_cross": 1,
            "bollinger": 1,
            "volume_surge": 1,
        }
    )


@dataclass
class SimulationConfig:
    enabled: bool = True
    initial_balance_usdt: float = 10.0
    position_size_pct: float = 100.0
    reserve_floor_eur: float = 6.0
    min_order_size_eur: float = 6.0
    floor_activation_threshold_eur: float = 12.0
    fee_pct: float = 0.1
    slippage_pct: float = 0.05
    duration_days: int = 14


@dataclass
class BacktestConfig:
    take_profit_pct: float = 1.5
    stop_loss_pct: float = 0.75
    max_holding_bars: int = 32
    slippage_pct: float = 0.05
    fee_pct: float = 0.075
    default_days: int = 30


@dataclass
class AppConfig:
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
    market: MarketConfig = field(default_factory=MarketConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    indicators: IndicatorsConfig = field(default_factory=IndicatorsConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    simulation: SimulationConfig = field(default_factory=SimulationConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)


def get_default_config_path() -> Path:
    """Devuelve la ruta absoluta por defecto al archivo de configuración."""
    return Path(__file__).resolve().parent.parent / "config" / "config.yaml"


def load_config(config_path: Optional[str] = None) -> AppConfig:
    """Carga y parsea la configuración de TradIA desde un archivo YAML."""
    path = Path(config_path) if config_path else get_default_config_path()

    if not path.exists():
        return AppConfig()

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    notif_raw = data.get("notifications", {})
    env_heartbeat = os.getenv("HEARTBEAT_EMAILS")
    if env_heartbeat is not None and env_heartbeat.strip() != "":
        heartbeat_enabled = env_heartbeat.strip().lower() in ("true", "1", "yes", "on")
    else:
        heartbeat_enabled = bool(notif_raw.get("heartbeat_emails", True))

    notif_cfg = NotificationsConfig(
        channel=str(notif_raw.get("channel", "email")).lower(),
        heartbeat_emails=heartbeat_enabled,
    )

    market_raw = data.get("market", {})
    market_cfg = MarketConfig(
        exchange=market_raw.get("exchange", "binance"),
        symbols=market_raw.get("symbols", ["BTC/USDT", "ETH/USDT"]),
        timeframe=market_raw.get("timeframe", "15m"),
    )

    sched_raw = data.get("schedule", {})
    windows_raw = sched_raw.get("windows", {})
    morning_raw = windows_raw.get("morning", {})
    evening_raw = windows_raw.get("evening", {})
    schedule_cfg = ScheduleConfig(
        timezone=sched_raw.get("timezone", "Europe/Madrid"),
        morning_window=WindowConfig(
            start=morning_raw.get("start", "08:30"),
            end=morning_raw.get("end", "17:30"),
        ),
        evening_window=WindowConfig(
            start=evening_raw.get("start", "20:30"),
            end=evening_raw.get("end", "23:00"),
        ),
        force_close_time=sched_raw.get("force_close_time", "22:45"),
        daily_report_time=sched_raw.get("daily_report_time", "23:00"),
        extended_hours_until=os.getenv("EXTENDED_HOURS_UNTIL") or sched_raw.get("extended_hours_until"),
    )

    ind_raw = data.get("indicators", {})
    indicators_cfg = IndicatorsConfig(
        rsi=RSIConfig(
            period=int(ind_raw.get("rsi", {}).get("period", 14)),
            oversold=float(ind_raw.get("rsi", {}).get("oversold", 30.0)),
            overbought=float(ind_raw.get("rsi", {}).get("overbought", 70.0)),
        ),
        ema=EMAConfig(
            fast_period=int(ind_raw.get("ema", {}).get("fast_period", 9)),
            slow_period=int(ind_raw.get("ema", {}).get("slow_period", 21)),
        ),
        macd=MACDConfig(
            fast_period=int(ind_raw.get("macd", {}).get("fast_period", 12)),
            slow_period=int(ind_raw.get("macd", {}).get("slow_period", 26)),
            signal_period=int(ind_raw.get("macd", {}).get("signal_period", 9)),
        ),
        bollinger=BollingerConfig(
            period=int(ind_raw.get("bollinger", {}).get("period", 20)),
            std_dev=float(ind_raw.get("bollinger", {}).get("std_dev", 2.0)),
        ),
        volume=VolumeConfig(
            sma_period=int(ind_raw.get("volume", {}).get("sma_period", 20)),
            multiplier_threshold=float(ind_raw.get("volume", {}).get("multiplier_threshold", 1.5)),
        ),
    )

    strat_raw = data.get("strategy", {})
    strategy_cfg = StrategyConfig(
        mode=strat_raw.get("mode", "scoring"),
        min_score_buy=int(strat_raw.get("min_score_buy", 3)),
        min_score_sell=int(strat_raw.get("min_score_sell", 3)),
        weights=strat_raw.get(
            "weights",
            {
                "rsi": 2,
                "ema_cross": 2,
                "macd_cross": 1,
                "bollinger": 1,
                "volume_surge": 1,
            },
        ),
    )

    sim_raw = data.get("simulation", {})
    reserve_floor = float(sim_raw.get("reserve_floor_eur", 6.0))
    min_order = float(sim_raw.get("min_order_size_eur", 6.0))
    activation_thresh = float(sim_raw.get("floor_activation_threshold_eur", reserve_floor + min_order))

    env_sim = os.getenv("SIMULATION_MODE") or os.getenv("SIMULATION_ENABLED")
    if env_sim is not None:
        sim_enabled = env_sim.strip().lower() in ("true", "1", "yes", "on")
    else:
        sim_enabled = bool(sim_raw.get("enabled", True))

    simulation_cfg = SimulationConfig(
        enabled=sim_enabled,
        initial_balance_usdt=float(sim_raw.get("initial_balance_usdt", 10.0)),
        position_size_pct=float(sim_raw.get("position_size_pct", 100.0)),
        reserve_floor_eur=reserve_floor,
        min_order_size_eur=min_order,
        floor_activation_threshold_eur=activation_thresh,
        fee_pct=float(sim_raw.get("fee_pct", 0.1)),
        slippage_pct=float(sim_raw.get("slippage_pct", 0.05)),
        duration_days=int(sim_raw.get("duration_days", 14)),
    )

    bt_raw = data.get("backtest", {})
    backtest_cfg = BacktestConfig(
        take_profit_pct=float(bt_raw.get("take_profit_pct", 1.5)),
        stop_loss_pct=float(bt_raw.get("stop_loss_pct", 0.75)),
        max_holding_bars=int(bt_raw.get("max_holding_bars", 32)),
        slippage_pct=float(bt_raw.get("slippage_pct", 0.05)),
        fee_pct=float(bt_raw.get("fee_pct", 0.075)),
        default_days=int(bt_raw.get("default_days", 30)),
    )

    return AppConfig(
        notifications=notif_cfg,
        market=market_cfg,
        schedule=schedule_cfg,
        indicators=indicators_cfg,
        strategy=strategy_cfg,
        simulation=simulation_cfg,
        backtest=backtest_cfg,
    )
