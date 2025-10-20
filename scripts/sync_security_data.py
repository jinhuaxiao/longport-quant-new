#!/usr/bin/env python3
"""简化版同步脚本：使用预定义的股票列表获取静态信息。"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from loguru import logger
from longport import OpenApiException, openapi
from sqlalchemy.dialects.postgresql import insert

from longport_quant.config import get_settings
from longport_quant.config.sdk import build_sdk_config
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.persistence.models import SecurityStatic, SecurityUniverse


# 预定义的热门股票列表
POPULAR_STOCKS = {
    "US": [
        # 科技股
        "AAPL.US", "MSFT.US", "GOOGL.US", "AMZN.US", "META.US",
        "NVDA.US", "TSLA.US", "AMD.US", "INTC.US", "CRM.US",
        "ORCL.US", "ADBE.US", "NFLX.US", "CSCO.US", "IBM.US",
        # 金融股
        "JPM.US", "BAC.US", "WFC.US", "GS.US", "MS.US",
        "C.US", "USB.US", "PNC.US", "AXP.US", "BLK.US",
        # 消费股
        "WMT.US", "HD.US", "DIS.US", "NKE.US", "MCD.US",
        "SBUX.US", "KO.US", "PEP.US", "PG.US", "JNJ.US",
        # ETF
        "SPY.US", "QQQ.US", "DIA.US", "IWM.US", "VTI.US",
        "VOO.US", "EEM.US", "GLD.US", "TLT.US", "XLF.US",
        # 中概股
        "BABA.US", "JD.US", "PDD.US", "BIDU.US", "NIO.US",
        "XPEV.US", "LI.US", "BILI.US", "IQ.US", "TME.US",
    ],
    "HK": [
        # 腾讯、阿里等
        "700.HK", "9988.HK", "1810.HK", "3690.HK", "9618.HK",
        # 银行股
        "939.HK", "1398.HK", "3988.HK", "2318.HK", "5.HK",
        # 内房股
        "2007.HK", "3333.HK", "1109.HK", "2202.HK",
        # 科技股
        "1810.HK", "2382.HK", "992.HK", "285.HK",
        # ETF
        "2800.HK", "2822.HK", "3033.HK", "2823.HK",
    ],
    "CN": [
        # A股主要指数ETF和个股示例
        "510050.SH", "510300.SH", "159915.SZ", "510500.SH",
    ]
}


@dataclass(frozen=True)
class SecurityInfo:
    symbol: str
    name_cn: Optional[str]
    name_en: Optional[str]
    name_hk: Optional[str]
    exchange: Optional[str]
    currency: Optional[str]
    lot_size: Optional[int]
    total_shares: Optional[int]
    circulating_shares: Optional[int]
    eps: Optional[Decimal]
    eps_ttm: Optional[Decimal]
    bps: Optional[Decimal]
    dividend_yield: Optional[Decimal]
    board: Optional[str]


def chunked(items: List[str], size: int) -> List[List[str]]:
    """将列表分批"""
    result = []
    for idx in range(0, len(items), size):
        result.append(items[idx : idx + size])
    return result


def fetch_securities_info(symbols: List[str], batch_size: int = 200) -> List[SecurityInfo]:
    """获取证券静态信息"""
    if not symbols:
        return []

    settings = get_settings()
    config = build_sdk_config(settings)
    ctx = openapi.QuoteContext(config)

    collected: List[SecurityInfo] = []

    try:
        for batch in chunked(symbols, batch_size):
            logger.info("获取 {} 个标的的静态信息...", len(batch))
            try:
                infos = ctx.static_info(batch)
                for info in infos:
                    symbol = getattr(info, "symbol", None)
                    if not symbol:
                        continue

                    collected.append(
                        SecurityInfo(
                            symbol=symbol,
                            name_cn=getattr(info, "name_cn", None),
                            name_en=getattr(info, "name_en", None),
                            name_hk=getattr(info, "name_hk", None),
                            exchange=getattr(info, "exchange", None),
                            currency=getattr(info, "currency", None),
                            lot_size=getattr(info, "lot_size", None),
                            total_shares=getattr(info, "total_shares", None),
                            circulating_shares=getattr(info, "circulating_shares", None),
                            eps=_to_decimal(getattr(info, "eps", None)),
                            eps_ttm=_to_decimal(getattr(info, "eps_ttm", None)),
                            bps=_to_decimal(getattr(info, "bps", None)),
                            dividend_yield=_to_decimal(getattr(info, "dividend_yield", None)),
                            board=str(getattr(info, "board", None)) if getattr(info, "board", None) else None,
                        )
                    )
            except OpenApiException as exc:
                logger.error("static_info 调用失败: {}", exc)
                continue

        logger.info("共获取 {} 条静态信息", len(collected))
        return collected

    finally:
        del ctx


def _to_decimal(value) -> Optional[Decimal]:
    """转换为 Decimal"""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ValueError, TypeError):
        return None


def _infer_market(symbol: str) -> str:
    """从股票代码推断市场"""
    if symbol.endswith(".US"):
        return "US"
    elif symbol.endswith(".HK"):
        return "HK"
    elif symbol.endswith(".SH") or symbol.endswith(".SZ"):
        return "CN"
    elif symbol.endswith(".SG"):
        return "SG"
    return "UNKNOWN"


async def persist_to_database(securities: List[SecurityInfo]) -> None:
    """持久化到数据库"""
    settings = get_settings()
    async with DatabaseSessionManager(settings.database_dsn) as db:
        await _upsert_universe(db, securities)
        await _upsert_static(db, securities)


async def _upsert_universe(db: DatabaseSessionManager, securities: List[SecurityInfo]) -> None:
    """更新 security_universe 表"""
    if not securities:
        return

    timestamp = datetime.utcnow()
    async with db.session() as session:
        for entry in securities:
            stmt = (
                insert(SecurityUniverse)
                .values(
                    symbol=entry.symbol,
                    market=_infer_market(entry.symbol),
                    name_cn=entry.name_cn,
                    name_en=entry.name_en,
                    name_hk=entry.name_hk,
                    updated_at=timestamp,
                )
                .on_conflict_do_update(
                    index_elements=["symbol"],
                    set_={
                        "market": _infer_market(entry.symbol),
                        "name_cn": entry.name_cn,
                        "name_en": entry.name_en,
                        "name_hk": entry.name_hk,
                        "updated_at": timestamp,
                    }
                )
            )
            await session.execute(stmt)
        await session.commit()
        logger.info("已写入 security_universe {} 条记录", len(securities))


async def _upsert_static(db: DatabaseSessionManager, securities: List[SecurityInfo]) -> None:
    """更新 security_static 表"""
    if not securities:
        return

    timestamp = datetime.utcnow()
    async with db.session() as session:
        for entry in securities:
            stmt = (
                insert(SecurityStatic)
                .values(
                    symbol=entry.symbol,
                    exchange=entry.exchange,
                    currency=entry.currency,
                    lot_size=entry.lot_size,
                    total_shares=entry.total_shares,
                    circulating_shares=entry.circulating_shares,
                    eps=entry.eps,
                    eps_ttm=entry.eps_ttm,
                    bps=entry.bps,
                    dividend_yield=entry.dividend_yield,
                    board=entry.board,
                    name_cn=entry.name_cn,
                    name_en=entry.name_en,
                    updated_at=timestamp,
                )
                .on_conflict_do_update(
                    index_elements=["symbol"],
                    set_={
                        "exchange": entry.exchange,
                        "currency": entry.currency,
                        "lot_size": entry.lot_size,
                        "total_shares": entry.total_shares,
                        "circulating_shares": entry.circulating_shares,
                        "eps": entry.eps,
                        "eps_ttm": entry.eps_ttm,
                        "bps": entry.bps,
                        "dividend_yield": entry.dividend_yield,
                        "board": str(entry.board) if entry.board else None,
                        "name_cn": entry.name_cn,
                        "name_en": entry.name_en,
                        "updated_at": timestamp,
                    }
                )
            )
            await session.execute(stmt)
        await session.commit()
        logger.info("已写入 security_static {} 条记录", len(securities))


def parse_args():
    parser = argparse.ArgumentParser(description="同步证券数据")
    parser.add_argument(
        "market",
        choices=["us", "hk", "cn", "all"],
        help="要同步的市场",
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        help="指定要同步的股票代码列表（覆盖默认列表）",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="每批次处理的股票数量（默认 200）",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # 确定要同步的股票列表
    if args.symbols:
        # 使用用户指定的股票
        symbols = args.symbols
    elif args.market == "all":
        # 同步所有市场
        symbols = []
        for market_symbols in POPULAR_STOCKS.values():
            symbols.extend(market_symbols)
    else:
        # 同步指定市场
        market_key = args.market.upper()
        symbols = POPULAR_STOCKS.get(market_key, [])

    if not symbols:
        logger.warning("没有要同步的股票")
        return

    logger.info("准备同步 {} 个股票", len(symbols))

    # 获取股票信息
    securities = fetch_securities_info(symbols, args.batch_size)

    # 保存到数据库
    asyncio.run(persist_to_database(securities))

    logger.info("同步任务完成")


if __name__ == "__main__":
    main()