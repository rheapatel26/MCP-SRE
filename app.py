import streamlit as st
import asyncio
import os
import json
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import google.generativeai as genai
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
    st.session_state.setdefault("available_models", ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-2.0-flash"])
    
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
    st.markdown("### How it works")
    st.info("1. Enter an instruction.\n2. AI generates MCP tool calls.\n3. The app executes them via the Playwright MCP server.")

# Initialize session state for logs and results
if "logs" not in st.session_state:
    st.session_state.logs = []
if "screenshot" not in st.session_state:
    st.session_state.screenshot = None

def log(message, type="info"):
    st.session_state.logs.append({"message": message, "type": type})

async def run_mcp_commands(commands):
    server_params = StdioServerParameters(
        command="npx",
        args=["-y", "@playwright/mcp@latest"],
        env=None
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            
            # List tools to verify connection
            tools = await session.list_tools()
            log(f"Connected to MCP Server. Available tools: {len(tools.tools)}", "success")

            results = []
            for cmd in commands:
                tool_name = cmd.get("tool")
                arguments = cmd.get("arguments", {})
                
                log(f"Executing tool: `{tool_name}` with arguments: `{json.dumps(arguments)}`", "step")
                
                try:
                    result = await session.call_tool(tool_name, arguments)
                    results.append(result)
                    
                    # Handle screenshot if present in result
                    # Note: @playwright/mcp usually returns base64 or paths
                    for content in result.content:
                        if content.type == "text":
                            log(f"Result: {content.text[:200]}...", "info")
                        elif content.type == "image":
                             st.session_state.screenshot = content.data
                except Exception as e:
                    log(f"Error calling tool `{tool_name}`: {str(e)}", "error")
                    break
            
            return results

def generate_script_from_nl(prompt, model_name):
    try:
        model = genai.GenerativeModel(model_name)
        
        system_prompt = """
        You are an expert browser automation engineer. Your task is to translate a natural language request into a sequence of MCP tool calls for the `@playwright/mcp` server.
        
        CRITICAL: The `@playwright/mcp` server tools like `browser_click` and `browser_type` require a "ref" (reference ID) from a previous `browser_snapshot`. 
        Since you are generating a static plan, the ONLY way to perform multiple actions (like navigations, clicks, and typing) reliably is to use the `browser_run_code` tool.
        
        `browser_run_code` takes a `code` argument which is a JavaScript function: `async (page) => { ... }`.
        
        Example for login:
        [
          {
            "tool": "browser_navigate", 
            "arguments": {"url": "https://github.com/login"}
          },
          {
            "tool": "browser_run_code",
            "arguments": {
              "code": "async (page) => { await page.fill('input[name=\"login\"]', 'user@example.com'); await page.fill('input[name=\"password\"]', 'secret'); await page.click('input[type=\"submit\"]'); }"
            }
          }
        ]
        
        Available tools:
        - browser_navigate(url: string)
        - browser_run_code(code: string): Use this for ALL clicks, typing, and complex sequences. The code MUST be an async function taking 'page'.
        - browser_snapshot(): Get accessibility info (only if user asks to see current state).
        - browser_screenshot_full_page(): Take a screenshot.
        
        Output ONLY a JSON array of objects, each containing "tool" and "arguments".
        """
        
        response = model.generate_content(f"{system_prompt}\n\nUser request: {prompt}")
        # Extract JSON from response
        text = response.text.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
            
        return json.loads(text)
    except Exception as e:
        st.error(f"Error generating script: {str(e)}")
        return None

# UI Input
user_input = st.text_area("What should the browser do?", placeholder="Navigate to google.com and search for 'MCP Protocol'")

if st.button("Execute Command", use_container_width=True):
    if user_input:
        st.session_state.logs = []
        st.session_state.screenshot = None
        
        with st.spinner(f"AI ({model_name}) is thinking..."):
            commands = generate_script_from_nl(user_input, model_name)
            
        if commands:
            st.success("Plan generated!")
            with st.expander("Show Generated Script"):
                st.json(commands)
            
            with st.spinner("Executing commands..."):
                asyncio.run(run_mcp_commands(commands))
    else:
        st.warning("Please enter a command.")

# Display Logs and Results
if st.session_state.logs:
    st.subheader("Execution Logs")
    for log_entry in st.session_state.logs:
        if log_entry["type"] == "step":
            st.info(log_entry["message"])
        elif log_entry["type"] == "success":
            st.success(log_entry["message"])
        elif log_entry["type"] == "error":
            st.error(log_entry["message"])
        else:
            st.write(log_entry["message"])

if st.session_state.screenshot:
    st.subheader("Latest Screenshot")
    st.image(st.session_state.screenshot)
