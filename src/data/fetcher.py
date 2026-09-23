"""Módulo de obtención de datos de mercado públicos.

GARANTÍA DE SEGURIDAD:
- Solo utiliza endpoints públicos de lectura.
- No utiliza ni permite claves privadas ni credenciales de trading.
"""
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import ccxt
import pandas as pd

logger = logging.getLogger(__name__)


class DataFetcher:
    """Cliente para descarga y cacheo de datos OHLCV históricos usando CCXT en modo público."""

    def __init__(self, exchange_id: str = "binance", cache_dir: Optional[str] = None):
        exchange_class = getattr(ccxt, exchange_id.lower(), None)
        if exchange_class is None:
            raise ValueError(f"Exchange '{exchange_id}' no soportado por ccxt.")

        # Inicialización explícita SIN credenciales (100% público)
        self.exchange = exchange_class({
            "enableRateLimit": True,
            "options": {
                "defaultType": "spot",  # Binance Spot público
            },
        })

        if cache_dir:
            self.cache_dir = Path(cache_dir)
        else:
            self.cache_dir = Path(__file__).resolve().parent.parent.parent / "data_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_cache_filepath(self, symbol: str, timeframe: str) -> Path:
        clean_symbol = symbol.replace("/", "_").replace(":", "_").upper()
        return self.cache_dir / f"{clean_symbol}_{timeframe}.csv"

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "15m",
        since_ms: Optional[int] = None,
        limit: int = 1000,
    ) -> pd.DataFrame:
        """Descarga un lote individual de velas OHLCV públicas."""
        try:
            raw_candles = self.exchange.fetch_ohlcv(
                symbol=symbol,
                timeframe=timeframe,
                since=since_ms,
                limit=limit,
            )
        except Exception as e:
            logger.error(f"Error al descargar datos para {symbol} ({timeframe}): {e}")
            raise

        if not raw_candles:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        df = pd.DataFrame(
            raw_candles,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        return df

    def fetch_historical(
        self,
        symbol: str,
        timeframe: str = "15m",
        days: int = 30,
        use_cache: bool = True,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """Descarga el histórico de velas para un período en días, gestionando paginación y caché local.

        Args:
            symbol: Par de trading (ej. 'BTC/USDT').
            timeframe: Temporalidad de la vela (ej. '15m').
            days: Cantidad de días hacia atrás a descargar.
            use_cache: Si es True, intenta leer/guardar en archivo local CSV.
            force_refresh: Si es True, ignora la caché existente y descarga de nuevo.

        Returns:
            DataFrame con columnas: [open, high, low, close, volume], indexado por timestamp UTC.
        """
        cache_path = self._get_cache_filepath(symbol, timeframe)
        now_utc = datetime.now(timezone.utc)
        start_dt = now_utc - timedelta(days=days)
        start_ms = int(start_dt.timestamp() * 1000)

        # 1. Intentar cargar desde caché si está disponible
        if use_cache and not force_refresh and cache_path.exists():
            try:
                cached_df = pd.read_csv(cache_path, parse_dates=["timestamp"])
                cached_df["timestamp"] = pd.to_datetime(cached_df["timestamp"], utc=True)
                cached_df.set_index("timestamp", inplace=True)
                cached_df.sort_index(inplace=True)

                if not cached_df.empty:
                    oldest = cached_df.index[0]
                    newest = cached_df.index[-1]
                    # Si cubre el rango solicitado (dentro de un margen de 2 velas), usarlo
                    if oldest <= start_dt and (now_utc - newest).total_seconds() < 3600 * 2:
                        logger.info(
                            f"Datos cargados desde caché para {symbol} ({timeframe}): "
                            f"{len(cached_df)} velas [{oldest} -> {newest}]"
                        )
                        return cached_df[cached_df.index >= start_dt]
            except Exception as e:
                logger.warning(f"No se pudo leer la caché de {cache_path}: {e}. Se descargará de nuevo.")

        # 2. Descarga paginada desde Binance público
        logger.info(f"Descargando histórico público de {symbol} ({timeframe}) para los últimos {days} días...")
        current_since = start_ms
        all_dfs = []
        batch_limit = 1000

        while True:
            batch_df = self.fetch_ohlcv(symbol, timeframe=timeframe, since_ms=current_since, limit=batch_limit)
            if batch_df.empty:
                break

            all_dfs.append(batch_df)

            last_ts = int(batch_df["timestamp"].iloc[-1].timestamp() * 1000)
            if len(batch_df) < batch_limit or last_ts >= int(now_utc.timestamp() * 1000) - 60000:
                break

            # Avanzar el timestamp para la siguiente página (último timestamp + 1ms)
            if last_ts == current_since:
                # Evitar bucle infinito si la API devuelve el mismo timestamp
                break
            current_since = last_ts + 1
            time.sleep(self.exchange.rateLimit / 1000.0)

        if not all_dfs:
            raise RuntimeError(f"No se obtuvieron velas públicas para {symbol} en el intervalo.")

        combined_df = pd.concat(all_dfs, ignore_index=True)
        combined_df.drop_duplicates(subset=["timestamp"], keep="last", inplace=True)
        combined_df.sort_values(by="timestamp", inplace=True)
        combined_df.set_index("timestamp", inplace=True)

        for col in ["open", "high", "low", "close", "volume"]:
            combined_df[col] = combined_df[col].astype(float)

        # 3. Guardar en caché si está habilitado
        if use_cache:
            try:
                combined_df.to_csv(cache_path, index=True)
                logger.info(f"Caché guardada en {cache_path} ({len(combined_df)} velas)")
            except Exception as e:
                logger.warning(f"No se pudo guardar la caché en {cache_path}: {e}")

        return combined_df
