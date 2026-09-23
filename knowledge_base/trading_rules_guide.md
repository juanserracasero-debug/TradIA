# Guía Maestra de Reglas y Criterios Técnicos — TradIA

Este documento consolida las mejores prácticas de trading algorítmico y discrecional para el ajuste manual de parámetros.

---

## 1. Filtrado de Tendencia (Cruces de Medias EMA 9 / 21)
- **Principio**: No operar en contra de la tendencia inmediata en velas de 15 minutos.
- **Regla Alcista**: Solo validar señales de COMPRA si la EMA rápida (9) está por encima de la EMA lenta (21), o en el momento exacto del cruce alcista.
- **Regla Bajista**: Si EMA rápida cruza por debajo de la lenta, desestimar compras por RSI sobrevendido (evitar "cuchillos cayendo").

---

## 2. Confirmación de Momento (MACD)
- **Principio**: La divergencia o el cruce de líneas valida la inercia del movimiento.
- **Regla**: Un cruce alcista de MACD sobre su línea de señal refuerza la probabilidad de éxito del trade y reduce señales falsas en rangos laterales estrechos.

---

## 3. Osciladores de Extremo (RSI 14 y Bandas de Bollinger)
- **Principio**: El RSI por debajo de 30 o el toque de la banda inferior indican compresión o agotamiento vendedor.
- **Advertencia Cuantitativa**: En caídas fuertes de mercado, el RSI puede permanecer sobrevendido durante horas. Por ello, **nunca debe ser la única condición**: siempre debe contar con el respaldo de volumen anómalo o reversión en velas.

---

## 4. Volumen como Validador de Intención Institucional
- **Principio**: Un movimiento de precio sin volumen suele carecer de continuidad.
- **Regla**: Exigir que el volumen de la vela de señal supere en al menos **1.5x (o 150%)** a su media móvil de 20 períodos para confirmar la entrada de capital significativo.

---

## 5. Gestión Estricta de Riesgo
- **Ratio Riesgo/Beneficio mínimo**: 1:2 (ejemplo: Stop Loss 0.75%, Take Profit 1.5%).
- **Cero pernocta intradía**: Todas las posiciones deben estar cerradas a las **22:45 hora Madrid** para evitar gaps nocturnos de baja liquidez.
