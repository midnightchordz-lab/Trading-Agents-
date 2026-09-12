#====================================================================================================
# START - Testing Protocol - DO NOT EDIT OR REMOVE THIS SECTION
#====================================================================================================

# THIS SECTION CONTAINS CRITICAL TESTING INSTRUCTIONS FOR BOTH AGENTS
# BOTH MAIN_AGENT AND TESTING_AGENT MUST PRESERVE THIS ENTIRE BLOCK

# Communication Protocol:
# If the `testing_agent` is available, main agent should delegate all testing tasks to it.
#
# You have access to a file called `test_result.md`. This file contains the complete testing state
# and history, and is the primary means of communication between main and the testing agent.
#
# Main and testing agents must follow this exact format to maintain testing data. 
# The testing data must be entered in yaml format Below is the data structure:
# 
## user_problem_statement: {problem_statement}
## backend:
##   - task: "Task name"
##     implemented: true
##     working: true  # or false or "NA"
##     file: "file_path.py"
##     stuck_count: 0
##     priority: "high"  # or "medium" or "low"
##     needs_retesting: false
##     status_history:
##         -working: true  # or false or "NA"
##         -agent: "main"  # or "testing" or "user"
##         -comment: "Detailed comment about status"
##
## frontend:
##   - task: "Task name"
##     implemented: true
##     working: true  # or false or "NA"
##     file: "file_path.js"
##     stuck_count: 0
##     priority: "high"  # or "medium" or "low"
##     needs_retesting: false
##     status_history:
##         -working: true  # or false or "NA"
##         -agent: "main"  # or "testing" or "user"
##         -comment: "Detailed comment about status"
##
## metadata:
##   created_by: "main_agent"
##   version: "1.0"
##   test_sequence: 0
##   run_ui: false
##
## test_plan:
##   current_focus:
##     - "Task name 1"
##     - "Task name 2"
##   stuck_tasks:
##     - "Task name with persistent issues"
##   test_all: false
##   test_priority: "high_first"  # or "sequential" or "stuck_first"
##
## agent_communication:
##     -agent: "main"  # or "testing" or "user"
##     -message: "Communication message between agents"

# Protocol Guidelines for Main agent
#
# 1. Update Test Result File Before Testing:
#    - Main agent must always update the `test_result.md` file before calling the testing agent
#    - Add implementation details to the status_history
#    - Set `needs_retesting` to true for tasks that need testing
#    - Update the `test_plan` section to guide testing priorities
#    - Add a message to `agent_communication` explaining what you've done
#
# 2. Incorporate User Feedback:
#    - When a user provides feedback that something is or isn't working, add this information to the relevant task's status_history
#    - Update the working status based on user feedback
#    - If a user reports an issue with a task that was marked as working, increment the stuck_count
#    - Whenever user reports issue in the app, if we have testing agent and task_result.md file so find the appropriate task for that and append in status_history of that task to contain the user concern and problem as well 
#
# 3. Track Stuck Tasks:
#    - Monitor which tasks have high stuck_count values or where you are fixing same issue again and again, analyze that when you read task_result.md
#    - For persistent issues, use websearch tool to find solutions
#    - Pay special attention to tasks in the stuck_tasks list
#    - When you fix an issue with a stuck task, don't reset the stuck_count until the testing agent confirms it's working
#
# 4. Provide Context to Testing Agent:
#    - When calling the testing agent, provide clear instructions about:
#      - Which tasks need testing (reference the test_plan)
#      - Any authentication details or configuration needed
#      - Specific test scenarios to focus on
#      - Any known issues or edge cases to verify
#
# 5. Call the testing agent with specific instructions referring to test_result.md
#
# IMPORTANT: Main agent must ALWAYS update test_result.md BEFORE calling the testing agent, as it relies on this file to understand what to test next.

#====================================================================================================
# END - Testing Protocol - DO NOT EDIT OR REMOVE THIS SECTION
#====================================================================================================



#====================================================================================================
# Testing Data - Main Agent and testing sub agent both should log testing data below this section
#====================================================================================================
## user_problem_statement: "Implement TRADINGVIEW_INTEGRATION_PART1B.md — in-app OHLC candlestick fallback chart for NSE/BSE symbols (which the free TradingView widget cannot show), fed by a new additive GET /api/ohlc/{symbol} endpoint. Draw the agents' verdict entry/target/stop-loss lines on the candles. Non-Indian symbols keep the TradingView widget unchanged. No new deps; pipeline/prompts/existing endpoints untouched."

## backend:
##   - task: "GET /api/ohlc/{symbol} endpoint (additive)"
##     implemented: true
##     working: true
##     file: "backend/server.py"
##     stuck_count: 0
##     priority: "high"
##     needs_retesting: true
##     status_history:
##         -working: true
##         -agent: "main"
##         -comment: "Appended fetch_ohlc_sync + /ohlc/{symbol} after /chart route. Returns {symbol,range,interval,currency,bars[]} with time/open/high/low/close/volume; unknown range -> 1M; bad symbol -> 404. Verified via curl (RELIANCE.NS 24 bars INR) and new tests/test_ohlc.py (3 pass). Full pytest suite 28 passed. git diff server.py is purely additive (no removed lines)."

## frontend:
##   - task: "NSE/BSE in-app Lightweight Charts fallback with verdict price lines"
##     implemented: true
##     working: "NA"
##     file: "frontend/src/components/LightweightChart.tsx, TradingViewChart.tsx, tv.ts, app/analysis/[id].tsx"
##     stuck_count: 0
##     priority: "high"
##     needs_retesting: true
##     status_history:
##         -working: "NA"
##         -agent: "main"
##         -comment: "TradingViewChart now routes NSE(.NS)/BSE(.BO)/^NSEI/^BSESN via widgetSupports() to the new LightweightChart (candles+volume, EMA20/50, BB20, RSI14 pane, MACD pane, 1D/1W/1M/1Y chips, verdict entry/target/stop price lines). Non-Indian symbols unchanged (widget). Card + range chips + indicator labels + RSI/MACD panes render in web preview but the candle canvas did not visibly paint in the screenshot (suspected canvas-in-sandboxed-iframe capture/timing quirk). Needs verification that candles actually draw."

## metadata:
##   created_by: "main_agent"
##   version: "1.1"
##   test_sequence: 3
##   run_ui: true

## test_plan:
##   current_focus:
##     - "GET /api/ohlc/{symbol} endpoint (additive)"
##     - "NSE/BSE in-app Lightweight Charts fallback with verdict price lines"
##   stuck_tasks: []
##   test_all: false
##   test_priority: "high_first"

## agent_communication:
##     -agent: "main"
##     -message: "Implemented Part1b. Backend /api/ohlc verified + all pytest pass. Please verify frontend: (1) NSE/BSE analysis (e.g. RELIANCE.NS id fe57f968-11a7-4dd9-b0b6-20acdb8d87de) VERDICT tab shows an in-app candlestick chart (CHART · OWN DATA) with candles actually drawn, RSI & MACD panes, working 1D/1W/1M/1Y chips, and BUY/SELL/HOLD entry + TARGET + STOP LOSS price lines — NO 'only available on TradingView' notice. (2) A widget symbol (GC=F id 09f6b956-b57c-4ebf-9c81-e1c1435cf3a7, or AAPL/BTC-USD) still renders the TradingView widget unchanged. Backend base URL from frontend/.env EXPO_PUBLIC_BACKEND_URL."
