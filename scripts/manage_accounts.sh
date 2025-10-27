#!/bin/bash

###########################################
# 多账号交易系统管理脚本
# 用于启动、停止和查看不同账号的交易进程
###########################################

set -e

# 项目根目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# 日志目录
LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"

# 配置目录
ACCOUNT_CONFIG_DIR="$PROJECT_ROOT/configs/accounts"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

###########################################
# 辅助函数
###########################################

print_header() {
    echo -e "${BLUE}╔══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║       Longport Quant - 多账号交易系统管理工具          ║${NC}"
    echo -e "${BLUE}╚══════════════════════════════════════════════════════════╝${NC}"
    echo
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

###########################################
# 检查账号配置是否存在
###########################################
check_account_config() {
    local account_id="$1"
    local config_file="$ACCOUNT_CONFIG_DIR/${account_id}.env"

    if [ ! -f "$config_file" ]; then
        print_error "账号配置文件不存在: $config_file"
        print_info "请先创建配置文件或参考 configs/accounts/README.md"
        return 1
    fi

    return 0
}

###########################################
# 列出所有可用账号
###########################################
list_accounts() {
    print_header
    echo -e "${BLUE}📁 可用账号配置:${NC}"
    echo

    if [ ! -d "$ACCOUNT_CONFIG_DIR" ]; then
        print_warning "账号配置目录不存在: $ACCOUNT_CONFIG_DIR"
        return
    fi

    local count=0
    for config_file in "$ACCOUNT_CONFIG_DIR"/*.env; do
        if [ -f "$config_file" ]; then
            local account_id=$(basename "$config_file" .env)
            local account_type="未知"

            if [[ "$account_id" == paper_* ]]; then
                account_type="模拟账号"
            elif [[ "$account_id" == live_* ]]; then
                account_type="真实账号"
            fi

            echo -e "  ${GREEN}•${NC} ${account_id} (${account_type})"
            count=$((count + 1))
        fi
    done

    if [ $count -eq 0 ]; then
        print_warning "没有找到任何账号配置文件"
        print_info "请在 $ACCOUNT_CONFIG_DIR 目录下创建账号配置文件"
    else
        echo
        print_success "共找到 $count 个账号配置"
    fi
}

###########################################
# 启动账号进程
###########################################
start_account() {
    local account_id="$1"

    if [ -z "$account_id" ]; then
        print_error "请指定账号ID"
        echo "用法: $0 start <account_id>"
        return 1
    fi

    # 检查配置文件
    if ! check_account_config "$account_id"; then
        return 1
    fi

    print_header
    echo -e "${GREEN}🚀 启动账号:${NC} $account_id"
    echo

    # 检查进程是否已经在运行
    local signal_pid=$(pgrep -f "signal_generator.py.*$account_id" || true)
    local executor_pid=$(pgrep -f "order_executor.py.*$account_id" || true)

    if [ -n "$signal_pid" ] || [ -n "$executor_pid" ]; then
        print_warning "账号 $account_id 的部分或全部进程已在运行:"
        [ -n "$signal_pid" ] && echo "  Signal Generator PID: $signal_pid"
        [ -n "$executor_pid" ] && echo "  Order Executor PID: $executor_pid"
        echo
        read -p "是否要重启进程? (y/n) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            stop_account "$account_id" || true
            sleep 2
        else
            return 0
        fi
    fi

    # 启动signal_generator
    print_info "启动 Signal Generator..."
    nohup python3 scripts/signal_generator.py --account-id "$account_id" \
        > "$LOG_DIR/signal_generator_${account_id}.log" 2>&1 &
    local sg_pid=$!
    sleep 1

    if ps -p $sg_pid > /dev/null; then
        print_success "Signal Generator 已启动 (PID: $sg_pid)"
    else
        print_error "Signal Generator 启动失败"
        print_info "查看日志: tail -f $LOG_DIR/signal_generator_${account_id}.log"
        return 1
    fi

    # 启动order_executor
    print_info "启动 Order Executor..."
    nohup python3 scripts/order_executor.py --account-id "$account_id" \
        > "$LOG_DIR/order_executor_${account_id}.log" 2>&1 &
    local oe_pid=$!
    sleep 1

    if ps -p $oe_pid > /dev/null; then
        print_success "Order Executor 已启动 (PID: $oe_pid)"
    else
        print_error "Order Executor 启动失败"
        print_info "查看日志: tail -f $LOG_DIR/order_executor_${account_id}.log"
        # 停止已启动的signal_generator
        kill $sg_pid 2>/dev/null || true
        return 1
    fi

    echo
    print_success "账号 $account_id 启动完成!"
    print_info "查看日志:"
    echo "  • Signal Generator: tail -f $LOG_DIR/signal_generator_${account_id}.log"
    echo "  • Order Executor:   tail -f $LOG_DIR/order_executor_${account_id}.log"
}

###########################################
# 停止账号进程
###########################################
stop_account() {
    local account_id="$1"

    if [ -z "$account_id" ]; then
        print_error "请指定账号ID"
        echo "用法: $0 stop <account_id>"
        return 1
    fi

    print_header
    echo -e "${RED}🛑 停止账号:${NC} $account_id"
    echo

    # 查找进程
    local signal_pids=$(pgrep -f "signal_generator.py.*$account_id" || true)
    local executor_pids=$(pgrep -f "order_executor.py.*$account_id" || true)

    if [ -z "$signal_pids" ] && [ -z "$executor_pids" ]; then
        print_warning "没有找到账号 $account_id 的运行进程"
        return 0
    fi

    # 停止signal_generator
    if [ -n "$signal_pids" ]; then
        print_info "停止 Signal Generator..."
        echo "$signal_pids" | while read pid; do
            kill -TERM "$pid" 2>/dev/null || true
            print_success "已发送停止信号到 PID: $pid"
        done
    fi

    # 停止order_executor
    if [ -n "$executor_pids" ]; then
        print_info "停止 Order Executor..."
        echo "$executor_pids" | while read pid; do
            kill -TERM "$pid" 2>/dev/null || true
            print_success "已发送停止信号到 PID: $pid"
        done
    fi

    # 等待进程退出
    print_info "等待进程退出..."
    sleep 2

    # 检查是否还有进程在运行
    local remaining_signal=$(pgrep -f "signal_generator.py.*$account_id" || true)
    local remaining_executor=$(pgrep -f "order_executor.py.*$account_id" || true)

    if [ -n "$remaining_signal" ] || [ -n "$remaining_executor" ]; then
        print_warning "部分进程未能正常退出，强制终止..."
        [ -n "$remaining_signal" ] && kill -9 $remaining_signal 2>/dev/null || true
        [ -n "$remaining_executor" ] && kill -9 $remaining_executor 2>/dev/null || true
    fi

    echo
    print_success "账号 $account_id 已停止"
}

###########################################
# 重启账号进程
###########################################
restart_account() {
    local account_id="$1"

    if [ -z "$account_id" ]; then
        print_error "请指定账号ID"
        echo "用法: $0 restart <account_id>"
        return 1
    fi

    stop_account "$account_id"
    sleep 1
    start_account "$account_id"
}

###########################################
# 查看账号状态
###########################################
status_account() {
    local account_id="$1"

    if [ -n "$account_id" ]; then
        # 查看指定账号状态
        print_header
        echo -e "${BLUE}📊 账号状态:${NC} $account_id"
        echo

        local signal_pid=$(pgrep -f "signal_generator.py.*$account_id" || true)
        local executor_pid=$(pgrep -f "order_executor.py.*$account_id" || true)

        echo -e "Signal Generator:  $([ -n "$signal_pid" ] && echo -e "${GREEN}运行中${NC} (PID: $signal_pid)" || echo -e "${RED}未运行${NC}")"
        echo -e "Order Executor:    $([ -n "$executor_pid" ] && echo -e "${GREEN}运行中${NC} (PID: $executor_pid)" || echo -e "${RED}未运行${NC}")"

        echo
        if [ -n "$signal_pid" ] || [ -n "$executor_pid" ]; then
            print_info "日志文件:"
            echo "  • Signal Generator: $LOG_DIR/signal_generator_${account_id}.log"
            echo "  • Order Executor:   $LOG_DIR/order_executor_${account_id}.log"
        fi
    else
        # 查看所有账号状态
        print_header
        echo -e "${BLUE}📊 所有账号状态:${NC}"
        echo

        if [ ! -d "$ACCOUNT_CONFIG_DIR" ]; then
            print_warning "账号配置目录不存在"
            return
        fi

        local found=0
        for config_file in "$ACCOUNT_CONFIG_DIR"/*.env; do
            if [ -f "$config_file" ]; then
                local acc_id=$(basename "$config_file" .env)
                local signal_pid=$(pgrep -f "signal_generator.py.*$acc_id" || true)
                local executor_pid=$(pgrep -f "order_executor.py.*$acc_id" || true)

                echo -e "${YELLOW}▶${NC} $acc_id"
                echo -e "  Signal Generator:  $([ -n "$signal_pid" ] && echo -e "${GREEN}运行中${NC} (PID: $signal_pid)" || echo -e "${RED}未运行${NC}")"
                echo -e "  Order Executor:    $([ -n "$executor_pid" ] && echo -e "${GREEN}运行中${NC} (PID: $executor_pid)" || echo -e "${RED}未运行${NC}")"
                echo

                found=1
            fi
        done

        if [ $found -eq 0 ]; then
            print_warning "没有找到任何账号配置"
        fi
    fi
}

###########################################
# 查看账号日志
###########################################
logs_account() {
    local account_id="$1"
    local service="$2"  # signal 或 executor

    if [ -z "$account_id" ]; then
        print_error "请指定账号ID"
        echo "用法: $0 logs <account_id> [signal|executor]"
        return 1
    fi

    if [ "$service" == "signal" ] || [ -z "$service" ]; then
        local log_file="$LOG_DIR/signal_generator_${account_id}.log"
        if [ -f "$log_file" ]; then
            print_info "查看 Signal Generator 日志 (按 Ctrl+C 退出):"
            tail -f "$log_file"
        else
            print_error "日志文件不存在: $log_file"
        fi
    elif [ "$service" == "executor" ]; then
        local log_file="$LOG_DIR/order_executor_${account_id}.log"
        if [ -f "$log_file" ]; then
            print_info "查看 Order Executor 日志 (按 Ctrl+C 退出):"
            tail -f "$log_file"
        else
            print_error "日志文件不存在: $log_file"
        fi
    else
        print_error "无效的服务类型: $service (应为 signal 或 executor)"
        return 1
    fi
}

###########################################
# 显示帮助信息
###########################################
show_help() {
    print_header
    echo "用法: $0 <command> [options]"
    echo
    echo "命令:"
    echo "  list                    列出所有可用的账号配置"
    echo "  start <account_id>      启动指定账号的交易进程"
    echo "  stop <account_id>       停止指定账号的交易进程"
    echo "  restart <account_id>    重启指定账号的交易进程"
    echo "  status [account_id]     查看账号状态（不指定则查看所有）"
    echo "  logs <account_id> [signal|executor]  查看账号日志"
    echo "  help                    显示此帮助信息"
    echo
    echo "示例:"
    echo "  $0 list                    # 列出所有账号"
    echo "  $0 start paper_001         # 启动模拟账号"
    echo "  $0 start live_001          # 启动真实账号"
    echo "  $0 stop paper_001          # 停止模拟账号"
    echo "  $0 status                  # 查看所有账号状态"
    echo "  $0 status paper_001        # 查看指定账号状态"
    echo "  $0 logs paper_001 signal   # 查看信号生成器日志"
    echo "  $0 logs paper_001 executor # 查看订单执行器日志"
}

###########################################
# 主函数
###########################################
main() {
    local command="$1"
    shift || true

    case "$command" in
        list)
            list_accounts
            ;;
        start)
            start_account "$@"
            ;;
        stop)
            stop_account "$@"
            ;;
        restart)
            restart_account "$@"
            ;;
        status)
            status_account "$@"
            ;;
        logs)
            logs_account "$@"
            ;;
        help|--help|-h|"")
            show_help
            ;;
        *)
            print_error "未知命令: $command"
            echo
            show_help
            exit 1
            ;;
    esac
}

# 执行主函数
main "$@"
