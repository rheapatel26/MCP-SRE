import streamlit as st
import asyncio
import os
import json
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import google.generativeai as genai
from google.api_core import exceptions
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

st.set_page_config(page_title="MCP Playwright Automator", layout="wide")

st.title("🤖 MCP Playwright Automator")
st.markdown("Transform your natural language commands into browser actions using MCP and Playwright.")

# Sidebar for configuration
with st.sidebar:
    st.header("Configuration")
    
    # Fetch API Key from environment
    api_key = os.getenv("GOOGLE_API_KEY")
    
    if not api_key:
        st.error("❌ `GOOGLE_API_KEY` not found in `.env` file.")
        st.stop()
    else:
        st.success("✅ API Key loaded from environment.")
        genai.configure(api_key=api_key)

    # Model selection and update
    st.session_state.setdefault("available_models", ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro", "gemini-2.5-flash"])
    
    model_name = st.selectbox(
        "Select Model",
        options=st.session_state.available_models,
        index=0,
        help="Select the Gemini model to use for translation."
    )
    
    if st.button("🔄 Update Model List"):
        try:
            models = [m.name.replace("models/", "") for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
            st.session_state.available_models = models
            st.rerun()
        except Exception as e:
            st.error(f"Error fetching models: {e}")

    st.divider()
    st.markdown("### Context-Aware Mode")
    smart_mode = st.toggle("Enable Semantic Analysis", value=True, help="Takes a snapshot of the page before planning actions to ensure correct locators.")

    st.divider()
    st.markdown("### How it works")
    st.info("1. Enter an instruction.\n2. App navigates and 'reads' the page semantically.\n3. AI generates resilient Playwright code.\n4. App executes and shows results.")

# Initialize session state for logs and results
if "logs" not in st.session_state:
    st.session_state.logs = []
if "screenshot" not in st.session_state:
    st.session_state.screenshot = None
if "snapshot" not in st.session_state:
    st.session_state.snapshot = None

def log(message, type="info"):
    st.session_state.logs.append({"message": message, "type": type})

async def execute_mcp_tool(session, tool_name, arguments):
    log(f"Executing tool: `{tool_name}` with arguments: `{json.dumps(arguments)}`", "step")
    try:
        result = await session.call_tool(tool_name, arguments)
        
        # Handle screenshot if present in result
        for content in result.content:
            if content.type == "text":
                log(f"Output: {content.text[:300]}...", "info")
            elif content.type == "image":
                 st.session_state.screenshot = content.data
        return result
    except Exception as e:
        log(f"Error calling tool `{tool_name}`: {str(e)}", "error")
        return None

def validate_plan(plan):
    """Validates that the plan is a dictionary with 'thought', 'tool_calls', and 'is_finished'."""
    if not isinstance(plan, dict):
        return False, "Plan is not an object."
    
    if "thought" not in plan:
        return False, "Plan is missing 'thought' key."
    if "is_finished" not in plan:
        return False, "Plan is missing 'is_finished' key."
    if "tool_calls" not in plan:
        return False, "Plan is missing 'tool_calls' key."
        
    if not isinstance(plan["tool_calls"], list):
        return False, "'tool_calls' is not a list."
    
    for i, cmd in enumerate(plan["tool_calls"]):
        if not isinstance(cmd, dict):
            return False, f"Command at index {i} in tool_calls is not an object."
        if "tool" not in cmd:
            return False, f"Command at index {i} in tool_calls is missing 'tool' key."
        if "arguments" not in cmd:
            return False, f"Command at index {i} in tool_calls is missing 'arguments' key."
            
    return True, ""

def generate_script_with_context(prompt, model_name, snapshot=None, history=None):
    model = genai.GenerativeModel(model_name)
    
    context_str = f"\n\nCURRENT PAGE SEMANTIC SNAPSHOT (Accessibility Tree):\n{snapshot}" if snapshot else ""
    history_str = f"\n\nACTION HISTORY (What you have done so far):\n{json.dumps(history, indent=2)}" if history else ""
    
    system_prompt = f"""
    You are an expert autonomous browser automation engineer. Your task is to achieve the user's goal by interacting with a website.
    You operate in a ReAct (Reasoning and Acting) loop. In each step, you analyze the current page state, look at your history, and decide on the NEXT interaction.
    
    {context_str}
    {history_str}
    
    CRITICAL RULES:
    1. Output ONLY a raw JSON object (no markdown code blocks).
    2. The JSON MUST have exactly three keys:
       - "thought": String. Your internal reasoning about the current state and what to do next.
       - "tool_calls": Array. A list of tool calls for this specific step. Keep this list short (1-3 actions).
       - "is_finished": Boolean. Set to true ONLY if the user's goal is fully achieved.
    3. Use `browser_run_code` for interactions. The 'code' argument MUST be a complete JavaScript async function literal.
       Example: "async (page) => {{ await page.getByRole('button', {{ name: 'Submit' }}).click(); }}"
    4. Use semantic locators whenever possible: `getByRole`, `getByText`, `getByPlaceholder`.
    
    AVAILABLE TOOLS:
    - browser_navigate(url: string)
    - browser_run_code(code: string)
    - browser_screenshot_full_page()
    - browser_snapshot(): Use this if you need to refresh your understanding of the page.
    
    OUTPUT FORMAT EXAMPLE:
    {{
      "thought": "The page has loaded. I see the search box. I will now enter the query.",
      "tool_calls": [
        {{"tool": "browser_run_code", "arguments": {{"code": "async (page) => {{ await page.getByRole('searchbox', {{ name: 'Search' }}).fill('MCP'); }}"}}}}
      ],
      "is_finished": false
    }}
    """
    
    try:
        response = model.generate_content(f"{system_prompt}\n\nUser request: {prompt}")
        text = response.text.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
        
        plan = json.loads(text)
        is_valid, error_msg = validate_plan(plan)
        if not is_valid:
            st.error(f"Invalid plan generated by AI: {error_msg}")
            return None
        return plan
    except exceptions.ResourceExhausted as e:
        st.error("⚠️ API Quota Exceeded (429). You are on the Gemini Free Tier which has a limit of 15-20 requests per day for some models. Please wait for the quota to reset or try a different model in the sidebar.")
        return None
    except exceptions.NotFound as e:
        st.error(f"❌ Model Not Found (404): The selected model '{model_name}' is not available or its name has changed. Try clicking 'Update Model List' in the sidebar or select a different model like 'gemini-2.0-flash'.")
        return None
    except Exception as e:
        st.error(f"Error generating script: {str(e)}")
        return None

async def run_smart_flow(user_prompt, model_name):
    import traceback
    script_path = os.path.join(os.getcwd(), "node_modules/@playwright/mcp/cli.js")
    if not os.path.exists(script_path):
        log(f"MCP script not found at {script_path}", "error")
        st.error(f"❌ MCP Server script not found at {script_path}. Have you run `npm install`?")
        return

    server_params = StdioServerParameters(
        command="node", 
        args=[script_path], 
        env=os.environ.copy()
    )
    
    history = []
    max_steps = 10
    step_count = 0
    
    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                log("Connected to MCP Server.", "success")
                
                while step_count < max_steps:
                    step_count += 1
                    log(f"--- Iteration {step_count} ---", "info")
                    
                    # Step 1: Observe (Snapshot)
                    log("Observing page state...", "step")
                    snapshot_result = await session.call_tool("browser_snapshot", {})
                    snapshot_text = snapshot_result.content[0].text if snapshot_result.content else ""
                    
                    # Step 2: Plan
                    log("Thinking and planning next step...", "step")
                    plan = generate_script_with_context(user_prompt, model_name, snapshot=snapshot_text, history=history)
                    
                    if not plan:
                        log("Planning failed. Stopping.", "error")
                        break
                        
                    log(f"🧠 AI Thought: {plan['thought']}", "info")
                    history.append({"thought": plan["thought"]})
                    
                    if plan.get("is_finished"):
                        log("Task completed successfully!", "success")
                        # Take final screenshot
                        await session.call_tool("browser_screenshot_full_page", {})
                        break
                        
                    # Step 3: Act
                    if not plan["tool_calls"]:
                        log("AI returned no tool calls but task is not finished. Retrying...", "info")
                        continue
                        
                    for cmd in plan["tool_calls"]:
                        tool_name = cmd["tool"]
                        args = cmd["arguments"]
                        result = await execute_mcp_tool(session, tool_name, args)
                        
                        # Add to history
                        history_entry = {"tool": tool_name, "arguments": args}
                        if result and hasattr(result, 'content') and result.content:
                            # Truncate content for history to avoid context bloat
                            history_entry["result"] = result.content[0].text[:500] if hasattr(result.content[0], 'text') else "Image data"
                        else:
                            history_entry["result"] = "No output or error"
                        history.append(history_entry)
                
                if step_count >= max_steps:
                    log("Max iterations reached. Task may be incomplete.", "warning")
                    
    except Exception as e:
        log(f"Connection/Execution Error: {str(e)}", "error")
        st.error(f"Detailed Error: {traceback.format_exc()}")
            
# UI Input
user_input = st.text_area("What should the browser do?", placeholder="Go to github.com and sign in...")

if st.button("Execute Command", use_container_width=True):
    if user_input:
        st.session_state.logs = []
        st.session_state.screenshot = None
        
        with st.spinner("Executing Context-Aware Automation..."):
            asyncio.run(run_smart_flow(user_input, model_name))
    else:
        st.warning("Please enter a command.")

# Display Logs and Results
if st.session_state.logs:
    st.subheader("Execution Progress")
    for log_entry in st.session_state.logs:
        if log_entry["type"] == "step":
            st.info(log_entry["message"])
        elif log_entry["type"] == "success":
            st.success(log_entry["message"])
        elif log_entry["type"] == "error":
            st.error(log_entry["message"])
        else:
            st.markdown(log_entry["message"])

if st.session_state.screenshot:
    st.divider()
    st.subheader("Final State View")
    st.image(st.session_state.screenshot)
