# TradingAgents/graph/trading_graph.py

import os
from pathlib import Path
import json
from datetime import date
from typing import Dict, Any, Tuple, List, Optional

from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI
from tradingagents.llm_adapters import ChatDashScope, ChatDashScopeOpenAI, ChatGoogleOpenAI

from langgraph.prebuilt import ToolNode

from tradingagents.agents import *
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.agents.utils.memory import FinancialSituationMemory

# 导入统一日志系统
from tradingagents.utils.logging_init import get_logger

# 导入日志模块
from tradingagents.utils.logging_manager import get_logger
logger = get_logger('agents')
from tradingagents.agents.utils.agent_states import (
    AgentState,
    InvestDebateState,
    RiskDebateState,
)
from tradingagents.dataflows.interface import set_config

from .conditional_logic import ConditionalLogic
from .setup import GraphSetup
from .propagation import Propagator
from .reflection import Reflector
from .signal_processing import SignalProcessor


class TradingAgentsGraph:
    """Main class that orchestrates the trading agents framework."""

    def __init__(
        self,
        selected_analysts=["market", "social", "news", "fundamentals"],
        debug=False,
        config: Dict[str, Any] = None,
    ):
        """初始化交易智能体图及其所有组件。

        Args:
            selected_analysts: 要包含的分析师类型列表，例如 ["market", "social", "news", "fundamentals"]。
            debug: 是否以调试模式运行。调试模式下可能会有更详细的日志输出。
            config: 配置字典。如果为 None，则使用默认配置 DEFAULT_CONFIG。
        """
        # 设置调试模式标志
        self.debug = debug
        # 初始化配置，如果未提供则使用默认配置
        self.config = config or DEFAULT_CONFIG

        # 更新数据流接口的配置，确保所有数据流组件使用最新的配置
        set_config(self.config)

        # 创建必要的目录，用于存储数据流的缓存文件
        # exist_ok=True 避免目录已存在时抛出错误
        os.makedirs(
            os.path.join(self.config["project_dir"], "dataflows/data_cache"),
            exist_ok=True,
        )

        # 初始化大型语言模型 (LLMs)
        # 根据配置中指定的LLM提供商，选择并实例化相应的LLM客户端
        if self.config["llm_provider"].lower() == "openai":
            # OpenAI 提供商配置
            self.deep_thinking_llm = ChatOpenAI(model=self.config["deep_think_llm"], base_url=self.config["backend_url"])
            self.quick_thinking_llm = ChatOpenAI(model=self.config["quick_think_llm"], base_url=self.config["backend_url"])
        elif self.config["llm_provider"] == "siliconflow":
            # SiliconFlow 支持：使用 OpenAI 兼容 API
            siliconflow_api_key = os.getenv('SILICONFLOW_API_KEY')
            # 检查 API 密钥是否已设置
            if not siliconflow_api_key:
                raise ValueError("使用SiliconFlow需要设置SILICONFLOW_API_KEY环境变量")

            logger.info(f"🌐 [SiliconFlow] 使用API密钥: {siliconflow_api_key[:20]}...")

            # 实例化用于深度思考和快速思考的LLM，并配置API密钥、基础URL、温度和最大token数
            self.deep_thinking_llm = ChatOpenAI(
                model=self.config["deep_think_llm"],
                base_url=self.config["backend_url"],
                api_key=siliconflow_api_key,
                temperature=0.1,
                max_tokens=2000
            )
            self.quick_thinking_llm = ChatOpenAI(
                model=self.config["quick_think_llm"],
                base_url=self.config["backend_url"],
                api_key=siliconflow_api_key,
                temperature=0.1,
                max_tokens=2000
            )
        elif self.config["llm_provider"] == "openrouter":
            # OpenRouter 支持：优先使用 OPENROUTER_API_KEY，否则使用 OPENAI_API_KEY
            openrouter_api_key = os.getenv('OPENROUTER_API_KEY') or os.getenv('OPENAI_API_KEY')
            # 检查 API 密钥是否已设置
            if not openrouter_api_key:
                raise ValueError("使用OpenRouter需要设置OPENROUTER_API_KEY或OPENAI_API_KEY环境变量")

            logger.info(f"🌐 [OpenRouter] 使用API密钥: {openrouter_api_key[:20]}...")

            # 实例化用于深度思考和快速思考的LLM
            self.deep_thinking_llm = ChatOpenAI(
                model=self.config["deep_think_llm"],
                base_url=self.config["backend_url"],
                api_key=openrouter_api_key
            )
            self.quick_thinking_llm = ChatOpenAI(
                model=self.config["quick_think_llm"],
                base_url=self.config["backend_url"],
                api_key=openrouter_api_key
            )
        elif self.config["llm_provider"] == "ollama":
            # Ollama 提供商配置
            self.deep_thinking_llm = ChatOpenAI(model=self.config["deep_think_llm"], base_url=self.config["backend_url"])
            self.quick_thinking_llm = ChatOpenAI(model=self.config["quick_think_llm"], base_url=self.config["backend_url"])
        elif self.config["llm_provider"].lower() == "anthropic":
            # Anthropic 提供商配置
            self.deep_thinking_llm = ChatAnthropic(model=self.config["deep_think_llm"], base_url=self.config["backend_url"])
            self.quick_thinking_llm = ChatAnthropic(model=self.config["quick_think_llm"], base_url=self.config["backend_url"])
        elif self.config["llm_provider"].lower() == "google":
            # 使用 Google OpenAI 兼容适配器，解决工具调用格式不匹配问题
            logger.info(f"🔧 使用Google AI OpenAI 兼容适配器 (解决工具调用问题)")
            google_api_key = os.getenv('GOOGLE_API_KEY')
            # 检查 API 密钥是否已设置
            if not google_api_key:
                raise ValueError("使用Google AI需要设置GOOGLE_API_KEY环境变量")
            
            # 实例化用于深度思考和快速思考的LLM，并配置API密钥、温度和最大token数
            self.deep_thinking_llm = ChatGoogleOpenAI(
                model=self.config["deep_think_llm"],
                google_api_key=google_api_key,
                temperature=0.1,
                max_tokens=2000
            )
            self.quick_thinking_llm = ChatGoogleOpenAI(
                model=self.config["quick_think_llm"],
                google_api_key=google_api_key,
                temperature=0.1,
                max_tokens=2000,
                client_options=client_options,
                transport="rest"
            )
            
            logger.info(f"✅ [Google AI] 已启用优化的工具调用和内容格式处理")
        elif (self.config["llm_provider"].lower() == "dashscope" or
              self.config["llm_provider"].lower() == "alibaba" or
              "dashscope" in self.config["llm_provider"].lower() or
              "阿里百炼" in self.config["llm_provider"]):
            # 阿里百炼 (DashScope) 提供商配置
            # 使用 OpenAI 兼容适配器，支持原生 Function Calling
            logger.info(f"🔧 使用阿里百炼 OpenAI 兼容适配器 (支持原生工具调用)")
            # 实例化用于深度思考和快速思考的LLM，并配置温度和最大token数
            self.deep_thinking_llm = ChatDashScopeOpenAI(
                model=self.config["deep_think_llm"],
                temperature=0.1,
                max_tokens=2000
            )
            self.quick_thinking_llm = ChatDashScopeOpenAI(
                model=self.config["quick_think_llm"],
                temperature=0.1,
                max_tokens=2000
            )
        elif (self.config["llm_provider"].lower() == "deepseek" or
              "deepseek" in self.config["llm_provider"].lower()):
            # DeepSeek V3 配置 - 使用支持 token 统计的适配器
            from tradingagents.llm_adapters.deepseek_adapter import ChatDeepSeek

            deepseek_api_key = os.getenv('DEEPSEEK_API_KEY')
            # 检查 API 密钥是否已设置
            if not deepseek_api_key:
                raise ValueError("使用DeepSeek需要设置DEEPSEEK_API_KEY环境变量")

            # 获取 DeepSeek 基础 URL，如果未设置则使用默认值
            deepseek_base_url = os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com')

            # 使用支持 token 统计的 DeepSeek 适配器实例化 LLM
            self.deep_thinking_llm = ChatDeepSeek(
                model=self.config["deep_think_llm"],
                api_key=deepseek_api_key,
                base_url=deepseek_base_url,
                temperature=0.1,
                max_tokens=2000
            )
            self.quick_thinking_llm = ChatDeepSeek(
                model=self.config["quick_think_llm"],
                api_key=deepseek_api_key,
                base_url=deepseek_base_url,
                temperature=0.1,
                max_tokens=2000
                )

            logger.info(f"✅ [DeepSeek] 已启用token统计功能")
        elif self.config["llm_provider"].lower() == "custom_openai":
            # 自定义 OpenAI 端点配置
            from tradingagents.llm_adapters.openai_compatible_base import create_openai_compatible_llm
            
            custom_api_key = os.getenv('CUSTOM_OPENAI_API_KEY')
            # 检查 API 密钥是否已设置
            if not custom_api_key:
                raise ValueError("使用自定义OpenAI端点需要设置CUSTOM_OPENAI_API_KEY环境变量")
            
            # 获取自定义 OpenAI 基础 URL，如果未设置则使用默认值
            custom_base_url = self.config.get("custom_openai_base_url", "https://api.openai.com/v1")
            
            logger.info(f"🔧 [自定义OpenAI] 使用端点: {custom_base_url}")
            
            # 使用 OpenAI 兼容适配器创建 LLM 实例
            self.deep_thinking_llm = create_openai_compatible_llm(
                provider="custom_openai",
                model=self.config["deep_think_llm"],
                base_url=custom_base_url,
                temperature=0.1,
                max_tokens=2000
            )
            self.quick_thinking_llm = create_openai_compatible_llm(
                provider="custom_openai",
                model=self.config["quick_think_llm"],
                base_url=custom_base_url,
                temperature=0.1,
                max_tokens=2000
            )
            
            logger.info(f"✅ [自定义OpenAI] 已配置自定义端点: {custom_base_url}")
        elif self.config["llm_provider"].lower() == "qianfan":
            # 百度千帆（文心一言）配置 - 统一由适配器内部读取与校验 QIANFAN_API_KEY
            from tradingagents.llm_adapters.openai_compatible_base import create_openai_compatible_llm
            
            # 使用OpenAI兼容适配器创建LLM实例（基类会使用千帆默认base_url并负责密钥校验）
            self.deep_thinking_llm = create_openai_compatible_llm(
                provider="qianfan",
                model=self.config["deep_think_llm"],
                temperature=0.1,
                max_tokens=2000
            )
            self.quick_thinking_llm = create_openai_compatible_llm(
                provider="qianfan",
                model=self.config["quick_think_llm"],
                temperature=0.1,
                max_tokens=2000
            )
            logger.info("✅ [千帆] 文心一言适配器已配置成功")
        else:
            # 如果配置了不支持的 LLM 提供商，则抛出错误
            raise ValueError(f"Unsupported LLM provider: {self.config['llm_provider']}")
        
        # 初始化工具包，其中包含各种数据获取和处理工具
        self.toolkit = Toolkit(config=self.config)

        # 初始化记忆模块 (如果配置中启用)
        memory_enabled = self.config.get("memory_enabled", True)
        if memory_enabled:
            # 使用单例 ChromaDB 管理器，避免并发创建冲突，为不同的智能体创建独立的记忆实例
            self.bull_memory = FinancialSituationMemory("bull_memory", self.config)          # 看涨分析师的记忆
            self.bear_memory = FinancialSituationMemory("bear_memory", self.config)          # 看跌分析师的记忆
            self.trader_memory = FinancialSituationMemory("trader_memory", self.config)      # 交易员的记忆
            self.invest_judge_memory = FinancialSituationMemory("invest_judge_memory", self.config) # 投资判断者的记忆
            self.risk_manager_memory = FinancialSituationMemory("risk_manager_memory", self.config) # 风险管理者的记忆
        else:
            # 如果记忆功能未启用，则将所有记忆对象设置为 None
            self.bull_memory = None
            self.bear_memory = None
            self.trader_memory = None
            self.invest_judge_memory = None
            self.risk_manager_memory = None

        # 创建工具节点，这些节点将工具封装起来，供 LangGraph 使用
        self.tool_nodes = self._create_tool_nodes()

        # 初始化核心组件
        self.conditional_logic = ConditionalLogic()  # 用于处理图中的条件逻辑和路由
        self.graph_setup = GraphSetup(               # 用于设置和构建 LangGraph
            self.quick_thinking_llm,                 # 快速思考LLM
            self.deep_thinking_llm,                  # 深度思考LLM
            self.toolkit,                            # 工具包
            self.tool_nodes,                         # 工具节点
            self.bull_memory,                        # 看涨分析师记忆
            self.bear_memory,                        # 看跌分析师记忆
            self.trader_memory,                      # 交易员记忆
            self.invest_judge_memory,                # 投资判断者记忆
            self.risk_manager_memory,                # 风险管理者记忆
            self.conditional_logic,                  # 条件逻辑处理器
            self.config,                             # 全局配置
            getattr(self, 'react_llm', None),        # ReAct 模式的LLM，如果存在
        )

        self.propagator = Propagator()               # 用于在图中传播状态和执行步骤
        self.reflector = Reflector(self.quick_thinking_llm) # 用于智能体反思和记忆更新
        self.signal_processor = SignalProcessor(self.quick_thinking_llm) # 用于处理和解析智能体输出的信号

        # 状态跟踪变量
        self.curr_state = None                       # 当前图的运行状态
        self.ticker = None                           # 当前分析的股票代码
        self.log_states_dict = {}                    # 存储日期到完整状态字典的映射，用于历史记录和调试

        # 设置并构建 LangGraph
        # 根据选定的分析师类型，动态构建交易智能体图
        self.graph = self.graph_setup.setup_graph(selected_analysts)

    def _create_tool_nodes(self) -> Dict[str, ToolNode]:
        """Create tool nodes for different data sources."""
        return {
            "market": ToolNode(
                [
                    # 统一工具
                    self.toolkit.get_stock_market_data_unified,
                    # online tools
                    self.toolkit.get_YFin_data_online,
                    self.toolkit.get_stockstats_indicators_report_online,
                    # offline tools
                    self.toolkit.get_YFin_data,
                    self.toolkit.get_stockstats_indicators_report,
                ]
            ),
            "social": ToolNode(
                [
                    # online tools
                    self.toolkit.get_stock_news_openai,
                    # offline tools
                    self.toolkit.get_reddit_stock_info,
                ]
            ),
            "news": ToolNode(
                [
                    # online tools
                    self.toolkit.get_global_news_openai,
                    self.toolkit.get_google_news,
                    # offline tools
                    self.toolkit.get_finnhub_news,
                    self.toolkit.get_reddit_news,
                ]
            ),
            "fundamentals": ToolNode(
                [
                    # 统一工具
                    self.toolkit.get_stock_fundamentals_unified,
                    # offline tools
                    self.toolkit.get_finnhub_company_insider_sentiment,
                    self.toolkit.get_finnhub_company_insider_transactions,
                    self.toolkit.get_simfin_balance_sheet,
                    self.toolkit.get_simfin_cashflow,
                    self.toolkit.get_simfin_income_stmt,
                ]
            ),
        }

    def propagate(self, company_name, trade_date):
        """Run the trading agents graph for a company on a specific date."""

        # 添加详细的接收日志
        logger.debug(f"🔍 [GRAPH DEBUG] ===== TradingAgentsGraph.propagate 接收参数 =====")
        logger.debug(f"🔍 [GRAPH DEBUG] 接收到的company_name: '{company_name}' (类型: {type(company_name)})")
        logger.debug(f"🔍 [GRAPH DEBUG] 接收到的trade_date: '{trade_date}' (类型: {type(trade_date)})")

        self.ticker = company_name
        logger.debug(f"🔍 [GRAPH DEBUG] 设置self.ticker: '{self.ticker}'")

        # Initialize state
        logger.debug(f"🔍 [GRAPH DEBUG] 创建初始状态，传递参数: company_name='{company_name}', trade_date='{trade_date}'")
        init_agent_state = self.propagator.create_initial_state(
            company_name, trade_date
        )
        logger.debug(f"🔍 [GRAPH DEBUG] 初始状态中的company_of_interest: '{init_agent_state.get('company_of_interest', 'NOT_FOUND')}'")
        logger.debug(f"🔍 [GRAPH DEBUG] 初始状态中的trade_date: '{init_agent_state.get('trade_date', 'NOT_FOUND')}'")
        args = self.propagator.get_graph_args()

        if self.debug:
            # Debug mode with tracing
            trace = []
            for chunk in self.graph.stream(init_agent_state, **args):
                if len(chunk["messages"]) == 0:
                    pass
                else:
                    chunk["messages"][-1].pretty_print()
                    trace.append(chunk)

            final_state = trace[-1]
        else:
            # Standard mode without tracing
            final_state = self.graph.invoke(init_agent_state, **args)

        # Store current state for reflection
        self.curr_state = final_state

        # Log state
        self._log_state(trade_date, final_state)

        # Return decision and processed signal
        return final_state, self.process_signal(final_state["final_trade_decision"], company_name)

    def _log_state(self, trade_date, final_state):
        """Log the final state to a JSON file."""
        self.log_states_dict[str(trade_date)] = {
            "company_of_interest": final_state["company_of_interest"],
            "trade_date": final_state["trade_date"],
            "market_report": final_state["market_report"],
            "sentiment_report": final_state["sentiment_report"],
            "news_report": final_state["news_report"],
            "fundamentals_report": final_state["fundamentals_report"],
            "investment_debate_state": {
                "bull_history": final_state["investment_debate_state"]["bull_history"],
                "bear_history": final_state["investment_debate_state"]["bear_history"],
                "history": final_state["investment_debate_state"]["history"],
                "current_response": final_state["investment_debate_state"][
                    "current_response"
                ],
                "judge_decision": final_state["investment_debate_state"][
                    "judge_decision"
                ],
            },
            "trader_investment_decision": final_state["trader_investment_plan"],
            "risk_debate_state": {
                "risky_history": final_state["risk_debate_state"]["risky_history"],
                "safe_history": final_state["risk_debate_state"]["safe_history"],
                "neutral_history": final_state["risk_debate_state"]["neutral_history"],
                "history": final_state["risk_debate_state"]["history"],
                "judge_decision": final_state["risk_debate_state"]["judge_decision"],
            },
            "investment_plan": final_state["investment_plan"],
            "final_trade_decision": final_state["final_trade_decision"],
        }

        # Save to file
        directory = Path(f"eval_results/{self.ticker}/TradingAgentsStrategy_logs/")
        directory.mkdir(parents=True, exist_ok=True)

        with open(
            f"eval_results/{self.ticker}/TradingAgentsStrategy_logs/full_states_log.json",
            "w",
        ) as f:
            json.dump(self.log_states_dict, f, indent=4)

    def reflect_and_remember(self, returns_losses):
        """Reflect on decisions and update memory based on returns."""
        self.reflector.reflect_bull_researcher(
            self.curr_state, returns_losses, self.bull_memory
        )
        self.reflector.reflect_bear_researcher(
            self.curr_state, returns_losses, self.bear_memory
        )
        self.reflector.reflect_trader(
            self.curr_state, returns_losses, self.trader_memory
        )
        self.reflector.reflect_invest_judge(
            self.curr_state, returns_losses, self.invest_judge_memory
        )
        self.reflector.reflect_risk_manager(
            self.curr_state, returns_losses, self.risk_manager_memory
        )

    def process_signal(self, full_signal, stock_symbol=None):
        """Process a signal to extract the core decision."""
        return self.signal_processor.process_signal(full_signal, stock_symbol)
