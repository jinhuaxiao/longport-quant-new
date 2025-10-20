"""Web API for monitoring dashboard."""

from __future__ import annotations

from typing import Dict, Any, Optional
from datetime import datetime, timedelta
import asyncio

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn
from pydantic import BaseModel
from loguru import logger

from longport_quant.monitoring.dashboard import MonitoringDashboard, SystemStatus


class DashboardAPI:
    """Web API for monitoring dashboard."""

    def __init__(self, dashboard: MonitoringDashboard):
        """
        Initialize dashboard API.

        Args:
            dashboard: Monitoring dashboard instance
        """
        self.dashboard = dashboard
        self.app = FastAPI(title="LongPort Quant Monitor", version="1.0.0")

        # Add CORS middleware
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # WebSocket connections
        self.websocket_connections: list[WebSocket] = []

        # Setup routes
        self._setup_routes()

    def _setup_routes(self):
        """Setup API routes."""

        @self.app.get("/")
        async def root():
            """API root endpoint."""
            return {
                "name": "LongPort Quant Monitor API",
                "version": "1.0.0",
                "status": self.dashboard.system_status.value,
                "timestamp": datetime.now().isoformat()
            }

        @self.app.get("/health")
        async def health_check():
            """Health check endpoint."""
            components = self.dashboard.component_status
            all_healthy = all(
                status.value == "healthy"
                for status in components.values()
            )

            return {
                "status": "healthy" if all_healthy else "degraded",
                "components": {
                    name: status.value
                    for name, status in components.items()
                },
                "timestamp": datetime.now().isoformat()
            }

        @self.app.get("/dashboard")
        async def get_dashboard():
            """Get complete dashboard data."""
            return self.dashboard.get_dashboard_data()

        @self.app.get("/system/status")
        async def get_system_status():
            """Get system status and metrics."""
            metrics = self.dashboard.system_metrics
            return {
                "status": self.dashboard.system_status.value,
                "metrics": {
                    "cpu_usage": metrics.cpu_usage,
                    "memory_usage": metrics.memory_usage,
                    "disk_usage": metrics.disk_usage,
                    "network_latency": metrics.network_latency,
                    "active_strategies": metrics.active_strategies,
                    "active_positions": metrics.active_positions,
                    "pending_orders": metrics.pending_orders,
                    "today_trades": metrics.today_trades,
                    "today_pnl": metrics.today_pnl,
                    "error_count": metrics.error_count,
                    "warning_count": metrics.warning_count,
                    "last_error": metrics.last_error,
                    "last_data_update": metrics.last_data_update.isoformat() if metrics.last_data_update else None
                }
            }

        @self.app.post("/system/control/{action}")
        async def control_system(action: str):
            """Control system operation."""
            if action == "start":
                await self.dashboard.start()
                return {"message": "System started", "status": self.dashboard.system_status.value}
            elif action == "stop":
                await self.dashboard.stop()
                return {"message": "System stopped", "status": self.dashboard.system_status.value}
            elif action == "pause":
                self.dashboard.system_status = SystemStatus.PAUSED
                return {"message": "System paused", "status": self.dashboard.system_status.value}
            elif action == "resume":
                self.dashboard.system_status = SystemStatus.RUNNING
                return {"message": "System resumed", "status": self.dashboard.system_status.value}
            else:
                raise HTTPException(status_code=400, detail=f"Invalid action: {action}")

        @self.app.get("/strategies")
        async def get_strategies():
            """Get strategy metrics."""
            return {
                strategy_name: {
                    "status": metrics.status.value,
                    "signals_generated": metrics.signals_generated,
                    "signals_executed": metrics.signals_executed,
                    "win_rate": metrics.win_rate,
                    "sharpe_ratio": metrics.sharpe_ratio,
                    "total_pnl": metrics.total_pnl,
                    "today_pnl": metrics.today_pnl,
                    "positions": metrics.positions,
                    "last_signal": metrics.last_signal.isoformat() if metrics.last_signal else None
                }
                for strategy_name, metrics in self.dashboard.strategy_metrics.items()
            }

        @self.app.get("/strategies/{strategy_name}")
        async def get_strategy(strategy_name: str):
            """Get specific strategy details."""
            if strategy_name not in self.dashboard.strategy_metrics:
                raise HTTPException(status_code=404, detail=f"Strategy not found: {strategy_name}")

            metrics = self.dashboard.strategy_metrics[strategy_name]
            return {
                "name": metrics.name,
                "status": metrics.status.value,
                "signals_generated": metrics.signals_generated,
                "signals_executed": metrics.signals_executed,
                "win_rate": metrics.win_rate,
                "sharpe_ratio": metrics.sharpe_ratio,
                "total_pnl": metrics.total_pnl,
                "today_pnl": metrics.today_pnl,
                "positions": metrics.positions,
                "last_signal": metrics.last_signal.isoformat() if metrics.last_signal else None
            }

        @self.app.get("/positions")
        async def get_positions():
            """Get position metrics."""
            return {
                symbol: {
                    "quantity": metrics.quantity,
                    "entry_price": metrics.entry_price,
                    "current_price": metrics.current_price,
                    "market_value": metrics.market_value,
                    "unrealized_pnl": metrics.unrealized_pnl,
                    "realized_pnl": metrics.realized_pnl,
                    "pnl_percent": metrics.pnl_percent,
                    "holding_period": str(metrics.holding_period),
                    "risk_level": metrics.risk_level.value
                }
                for symbol, metrics in self.dashboard.position_metrics.items()
            }

        @self.app.get("/positions/{symbol}")
        async def get_position(symbol: str):
            """Get specific position details."""
            if symbol not in self.dashboard.position_metrics:
                raise HTTPException(status_code=404, detail=f"Position not found: {symbol}")

            metrics = self.dashboard.position_metrics[symbol]
            return {
                "symbol": metrics.symbol,
                "quantity": metrics.quantity,
                "entry_price": metrics.entry_price,
                "current_price": metrics.current_price,
                "market_value": metrics.market_value,
                "unrealized_pnl": metrics.unrealized_pnl,
                "realized_pnl": metrics.realized_pnl,
                "pnl_percent": metrics.pnl_percent,
                "holding_period": str(metrics.holding_period),
                "risk_level": metrics.risk_level.value
            }

        @self.app.get("/market")
        async def get_market():
            """Get market overview."""
            return {
                symbol: {
                    "last_price": metrics.last_price,
                    "change_percent": metrics.change_percent,
                    "volume": metrics.volume,
                    "bid": metrics.bid,
                    "ask": metrics.ask,
                    "spread": metrics.spread,
                    "volatility": metrics.volatility,
                    "signal_strength": metrics.signal_strength,
                    "last_update": metrics.last_update.isoformat() if metrics.last_update else None
                }
                for symbol, metrics in self.dashboard.market_metrics.items()
            }

        @self.app.get("/market/{symbol}")
        async def get_market_symbol(symbol: str):
            """Get market data for specific symbol."""
            if symbol not in self.dashboard.market_metrics:
                raise HTTPException(status_code=404, detail=f"Market data not found: {symbol}")

            metrics = self.dashboard.market_metrics[symbol]
            return {
                "symbol": metrics.symbol,
                "last_price": metrics.last_price,
                "change_percent": metrics.change_percent,
                "volume": metrics.volume,
                "bid": metrics.bid,
                "ask": metrics.ask,
                "spread": metrics.spread,
                "volatility": metrics.volatility,
                "signal_strength": metrics.signal_strength,
                "last_update": metrics.last_update.isoformat() if metrics.last_update else None
            }

        @self.app.get("/risk")
        async def get_risk_metrics():
            """Get risk metrics."""
            risk_metrics = self.dashboard.risk_engine.get_risk_metrics()
            return {
                "portfolio_value": risk_metrics.portfolio_value,
                "cash_available": risk_metrics.cash_available,
                "long_exposure": risk_metrics.long_exposure,
                "short_exposure": risk_metrics.short_exposure,
                "gross_exposure": risk_metrics.gross_exposure,
                "net_exposure": risk_metrics.net_exposure,
                "current_drawdown": risk_metrics.current_drawdown,
                "daily_pnl": risk_metrics.daily_pnl,
                "daily_trades": risk_metrics.daily_trades,
                "var_95": risk_metrics.var_95,
                "sharpe_ratio": risk_metrics.sharpe_ratio,
                "risk_level": risk_metrics.risk_level.value,
                "position_concentration": risk_metrics.position_concentration
            }

        @self.app.get("/alerts")
        async def get_alerts(level: Optional[str] = None):
            """Get active alerts."""
            alerts = self.dashboard.active_alerts

            if level:
                alerts = [a for a in alerts if a.level == level.upper()]

            return [
                {
                    "timestamp": alert.timestamp.isoformat(),
                    "level": alert.level,
                    "category": alert.category,
                    "message": alert.message,
                    "details": alert.details
                }
                for alert in alerts[-50:]  # Last 50 alerts
            ]

        @self.app.get("/performance")
        async def get_performance():
            """Get performance summary."""
            return self.dashboard.get_performance_summary()

        @self.app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            """WebSocket endpoint for real-time updates."""
            await websocket.accept()
            self.websocket_connections.append(websocket)

            try:
                # Send updates every second
                while True:
                    data = {
                        "type": "update",
                        "timestamp": datetime.now().isoformat(),
                        "system_status": self.dashboard.system_status.value,
                        "positions": len(self.dashboard.position_metrics),
                        "today_pnl": self.dashboard.system_metrics.today_pnl,
                        "alerts": len(self.dashboard.active_alerts)
                    }
                    await websocket.send_json(data)
                    await asyncio.sleep(1)

            except WebSocketDisconnect:
                self.websocket_connections.remove(websocket)
                logger.info("WebSocket client disconnected")
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                if websocket in self.websocket_connections:
                    self.websocket_connections.remove(websocket)

        @self.app.exception_handler(Exception)
        async def general_exception_handler(request, exc):
            """Handle all uncaught exceptions."""
            logger.error(f"Unhandled exception: {exc}")
            return JSONResponse(
                status_code=500,
                content={
                    "error": "Internal server error",
                    "message": str(exc),
                    "timestamp": datetime.now().isoformat()
                }
            )

    async def broadcast_update(self, data: Dict[str, Any]):
        """Broadcast update to all WebSocket connections."""
        disconnected = []
        for websocket in self.websocket_connections:
            try:
                await websocket.send_json(data)
            except Exception:
                disconnected.append(websocket)

        # Remove disconnected clients
        for websocket in disconnected:
            self.websocket_connections.remove(websocket)

    def run(self, host: str = "0.0.0.0", port: int = 8000):
        """Run the API server."""
        logger.info(f"Starting monitoring API on {host}:{port}")
        uvicorn.run(self.app, host=host, port=port)


class AlertRequest(BaseModel):
    """Request model for creating alerts."""
    level: str
    category: str
    message: str
    details: Optional[Dict[str, Any]] = None


class SystemControlRequest(BaseModel):
    """Request model for system control."""
    action: str  # start, stop, pause, resume