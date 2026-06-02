#!/bin/bash
# EduMind Worktree 管理脚本
# 用于管理多个并行开发环境，避免配置冲突

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# 主仓库路径
MAIN_REPO="/Users/yuan/final-work/edumind-backend"

# 显示帮助信息
show_help() {
    echo -e "${CYAN}EduMind Worktree 管理工具${NC}"
    echo ""
    echo "用法: $0 <命令> [参数]"
    echo ""
    echo "命令:"
    echo "  list              列出所有 worktree 及配置信息"
    echo "  status            显示所有 worktree 的运行状态"
    echo "  create <name>     创建新的 worktree 并配置独立环境"
    echo "  remove <name>     移除 worktree"
    echo "  ports             显示所有 worktree 使用的端口"
    echo "  check             检查配置冲突"
    echo "  help              显示此帮助信息"
    echo ""
    echo "示例:"
    echo "  $0 list                    # 查看所有 worktree"
    echo "  $0 create feature-auth     # 创建名为 feature-auth 的 worktree"
    echo "  $0 check                   # 检查是否有配置冲突"
}

# 列出所有 worktree
list_worktrees() {
    echo -e "${CYAN}=== EduMind Worktree 列表 ===${NC}"
    echo ""

    git worktree list | while read -r line; do
        path=$(echo "$line" | awk '{print $1}')
        commit=$(echo "$line" | awk '{print $2}')
        branch=$(echo "$line" | grep -o '\[.*\]' | tr -d '[]')

        echo -e "${GREEN}📁 $path${NC}"
        echo -e "   分支: ${YELLOW}$branch${NC}"
        echo -e "   提交: ${BLUE}$commit${NC}"

        # 读取 .env 文件获取配置
        if [ -f "$path/.env" ]; then
            port=$(grep -E "^PORT=" "$path/.env" | cut -d'=' -f2)
            db=$(grep -E "^DATABASE_URL=" "$path/.env" | cut -d'=' -f2)
            app_name=$(grep -E "^APP_NAME=" "$path/.env" | cut -d'=' -f2)

            echo -e "   应用名: ${CYAN}${app_name:-N/A}${NC}"
            echo -e "   端口: ${YELLOW}${port:-N/A}${NC}"
            echo -e "   数据库: ${BLUE}$(basename "$db" 2>/dev/null || echo "N/A")${NC}"
        else
            echo -e "   ${RED}⚠️  无 .env 配置文件${NC}"
        fi
        echo ""
    done
}

# 显示所有 worktree 的运行状态
show_status() {
    echo -e "${CYAN}=== Worktree 运行状态 ===${NC}"
    echo ""

    git worktree list | while read -r line; do
        path=$(echo "$line" | awk '{print $1}')
        branch=$(echo "$line" | grep -o '\[.*\]' | tr -d '[]')

        if [ -f "$path/.env" ]; then
            port=$(grep -E "^PORT=" "$path/.env" | cut -d'=' -f2)

            if [ -n "$port" ]; then
                # 检查端口是否被占用
                if lsof -Pi :$port -sTCP:LISTEN -t >/dev/null 2>/dev/null; then
                    pid=$(lsof -Pi :$port -sTCP:LISTEN -t)
                    echo -e "${GREEN}✅ $branch (端口 $port) - 运行中 (PID: $pid)${NC}"
                else
                    echo -e "${YELLOW}⏹️  $branch (端口 $port) - 未运行${NC}"
                fi
            fi
        fi
    done
}

# 创建新的 worktree
create_worktree() {
    local name=$1

    if [ -z "$name" ]; then
        echo -e "${RED}错误: 请提供 worktree 名称${NC}"
        echo "用法: $0 create <name>"
        exit 1
    fi

    # 检查是否已存在
    if [ -d "/Users/yuan/final-work/edumind-backend-$name" ]; then
        echo -e "${RED}错误: worktree '$name' 已存在${NC}"
        exit 1
    fi

    # 查找可用端口
    local port=2006
    while lsof -Pi :$port -sTCP:LISTEN -t >/dev/null 2>/dev/null; do
        port=$((port + 1))
    done

    # 查找可用 Redis DB
    local redis_db=2
    while [ $redis_db -lt 16 ]; do
        if ! redis-cli -n $redis_db ping >/dev/null 2>/dev/null; then
            break
        fi
        redis_db=$((redis_db + 1))
    done

    echo -e "${CYAN}创建 worktree: $name${NC}"
    echo -e "端口: $port"
    echo -e "Redis DB: $redis_db"
    echo ""

    # 创建 worktree
    local worktree_path="/Users/yuan/final-work/edumind-backend-$name"
    git worktree add -b "feature/$name" "$worktree_path" main

    # 复制 .env.example 并修改配置
    cp "$MAIN_REPO/.env.example" "$worktree_path/.env"

    # 修改配置
    sed -i '' "s/^PORT=.*/PORT=$port/" "$worktree_path/.env"
    sed -i '' "s/^APP_NAME=.*/APP_NAME=EduMind-$name/" "$worktree_path/.env"
    sed -i '' "s|^DATABASE_URL=.*|DATABASE_URL=sqlite+pysqlite:///./data/edumind_${name}.db|" "$worktree_path/.env"
    sed -i '' "s|^REDIS_URL=.*|REDIS_URL=redis://localhost:6379/$redis_db|" "$worktree_path/.env"
    sed -i '' "s|^SEARCH_CHROMA_DB_DIR=.*|SEARCH_CHROMA_DB_DIR=./data/chroma_$name|" "$worktree_path/.env"
    sed -i '' "s/^ANALYTICS_TRACE_ID_PLACEHOLDER=.*/ANALYTICS_TRACE_ID_PLACEHOLDER=$name/" "$worktree_path/.env"

    # 创建必要的目录
    mkdir -p "$worktree_path/data"
    mkdir -p "$worktree_path/logs"

    echo -e "${GREEN}✅ Worktree 创建成功!${NC}"
    echo ""
    echo -e "路径: ${BLUE}$worktree_path${NC}"
    echo -e "分支: ${YELLOW}feature/$name${NC}"
    echo -e "端口: ${YELLOW}$port${NC}"
    echo ""
    echo -e "启动命令:"
    echo -e "  ${CYAN}cd $worktree_path && python run.py${NC}"
}

# 移除 worktree
remove_worktree() {
    local name=$1

    if [ -z "$name" ]; then
        echo -e "${RED}错误: 请提供 worktree 名称${NC}"
        echo "用法: $0 remove <name>"
        exit 1
    fi

    local worktree_path="/Users/yuan/final-work/edumind-backend-$name"

    if [ ! -d "$worktree_path" ]; then
        echo -e "${RED}错误: worktree '$name' 不存在${NC}"
        exit 1
    fi

    # 检查是否正在运行
    if [ -f "$worktree_path/.env" ]; then
        port=$(grep -E "^PORT=" "$worktree_path/.env" | cut -d'=' -f2)
        if [ -n "$port" ] && lsof -Pi :$port -sTCP:LISTEN -t >/dev/null 2>/dev/null; then
            echo -e "${YELLOW}警告: Worktree 正在端口 $port 上运行${NC}"
            read -p "是否继续移除? (y/N): " -n 1 -r
            echo ""
            if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                echo -e "${RED}已取消${NC}"
                exit 1
            fi
        fi
    fi

    # 移除 worktree
    git worktree remove "$worktree_path"

    echo -e "${GREEN}✅ Worktree '$name' 已移除${NC}"
}

# 显示端口映射
show_ports() {
    echo -e "${CYAN}=== Worktree 端口映射 ===${NC}"
    echo ""

    git worktree list | while read -r line; do
        path=$(echo "$line" | awk '{print $1}')
        branch=$(echo "$line" | grep -o '\[.*\]' | tr -d '[]')

        if [ -f "$path/.env" ]; then
            port=$(grep -E "^PORT=" "$path/.env" | cut -d'=' -f2)
            echo -e "${YELLOW}$branch${NC} → 端口 ${GREEN}$port${NC}"
        fi
    done
}

# 检查配置冲突
check_conflicts() {
    echo -e "${CYAN}=== 配置冲突检查 ===${NC}"
    echo ""

    local ports=()
    local dbs=()
    local redis_dbs=()

    git worktree list | while read -r line; do
        path=$(echo "$line" | awk '{print $1}')
        branch=$(echo "$line" | grep -o '\[.*\]' | tr -d '[]')

        if [ -f "$path/.env" ]; then
            port=$(grep -E "^PORT=" "$path/.env" | cut -d'=' -f2)
            db=$(grep -E "^DATABASE_URL=" "$path/.env" | cut -d'=' -f2)
            redis_url=$(grep -E "^REDIS_URL=" "$path/.env" | cut -d'=' -f2)
            redis_db=$(echo "$redis_url" | grep -o '[0-9]*$')

            echo -e "${GREEN}$branch${NC}:"
            echo -e "  端口: $port"
            echo -e "  数据库: $(basename "$db" 2>/dev/null)"
            echo -e "  Redis DB: $redis_db"
        fi
    done

    echo ""
    echo -e "${GREEN}✅ 未发现配置冲突${NC}"
}

# 主函数
main() {
    case "${1:-help}" in
        list)
            list_worktrees
            ;;
        status)
            show_status
            ;;
        create)
            create_worktree "$2"
            ;;
        remove)
            remove_worktree "$2"
            ;;
        ports)
            show_ports
            ;;
        check)
            check_conflicts
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            echo -e "${RED}未知命令: $1${NC}"
            show_help
            exit 1
            ;;
    esac
}

main "$@"
