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
    """Validates that the plan is a list of dictionaries with 'tool' and 'arguments'."""
    if not isinstance(plan, list):
        return False, "Plan is not a list."
    
    for i, cmd in enumerate(plan):
        if not isinstance(cmd, dict):
            return False, f"Command at index {i} is not a object."
        if "tool" not in cmd:
            return False, f"Command at index {i} is missing 'tool' key."
        if "arguments" not in cmd:
            return False, f"Command at index {i} is missing 'arguments' key."
            
    return True, ""

def generate_script_with_context(prompt, model_name, snapshot=None):
    model = genai.GenerativeModel(model_name)
    
    context_str = f"\n\nPAGE SEMANTIC SNAPSHOT (Accessibility Tree):\n{snapshot}" if snapshot else ""
    
    system_prompt = f"""
    You are an expert browser automation engineer. Your task is to translate a natural language request into a sequence of tool calls.
    
    {context_str}
    
    CRITICAL: Use `browser_run_code` for interactions. The 'code' argument MUST be a complete JavaScript async function literal.
    Example: "async (page) => {{ await page.getByRole('button', {{ name: 'Submit' }}).click(); }}"
    
    Use semantic locators whenever possible:
    - `page.getByRole(role, {{ name: '...' }})`
    - `page.getByText('...')`
    - `page.getByPlaceholder('...')`
    
    If the snapshot doesn't contain the target, write code to search for it first.
    
    Available tools:
    - browser_navigate(url: string)
    - browser_run_code(code: string)
    - browser_screenshot_full_page()
    
    Output ONLY a JSON array of tool calls. Each tool call MUST be an object with "tool" and "arguments" keys.
    Example: [{{"tool": "browser_navigate", "arguments": {{"url": "https://example.com"}}}}, {{"tool": "browser_run_code", "arguments": {{"code": "async (page) => {{ await page.goto('https://example.com'); }}"}}}}]
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
    
    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                log("Connected to MCP Server.", "success")
                
                # Step 1: Initial Planning (Navigate only)
                initial_plan = generate_script_with_context(f"Navigate to the main site for: {user_prompt}", model_name)
                
                if initial_plan:
                    # Execute navigation
                    for cmd in initial_plan:
                        if cmd.get("tool") == "browser_navigate":
                            await execute_mcp_tool(session, cmd["tool"], cmd["arguments"])
                    
                    # Step 2: Semantic Analysis
                    log("Analyzing page semantic structure...", "step")
                    snapshot_result = await session.call_tool("browser_snapshot", {})
                    snapshot_text = snapshot_result.content[0].text if snapshot_result.content else ""
                    
                    # Step 3: Final Planning with Context
                    log("Planning interactions based on semantic data...", "step")
                    final_plan = generate_script_with_context(user_prompt, model_name, snapshot=snapshot_text)
                    
                    if final_plan:
                        with st.expander("Show Semantic Plan"):
                            st.json(final_plan)
                        
                        # Execute remaining steps
                        for cmd in final_plan:
                            if cmd.get("tool") != "browser_navigate":
                                await execute_mcp_tool(session, cmd.get("tool"), cmd.get("arguments"))
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
