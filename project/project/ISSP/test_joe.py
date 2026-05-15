import os
import sys

# Ensure the script can find your 'services' and 'config' files
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from services.llm_client import ask_llm

def run_local_test():
    print("==================================================")
    print("🤖 JOE LOCAL TESTING TERMINAL 🤖")
    print("Test your Azure Search RAG & LLM logic instantly.")
    print("Type 'quit' or 'exit' to stop.")
    print("==================================================\n")

    # Keep track of local history just like the Teams route does
    history = []
    MAX_TURNS = 10

    while True:
        try:
            question = input("\nYou: ").strip()
            
            if question.lower() in ['quit', 'exit']:
                print("\nShutting down test terminal...")
                break
            
            if not question:
                continue

            print("Joe is searching Azure and thinking...")
            
            # 1. Call the exact same LLM function your bot uses
            answer = ask_llm(question, history=history)
            
            # 2. Print the result to your terminal
            print(f"\nJoe: {answer}")

            # 3. Update local history window
            history.append({"role": "user", "content": question})
            history.append({"role": "assistant", "content": answer})
            
            if len(history) > MAX_TURNS * 2:
                history = history[-(MAX_TURNS * 2):]

        except KeyboardInterrupt:
            print("\nShutting down test terminal...")
            break
        except Exception as e:
            print(f"\n[Crash Error]: {e}")

if __name__ == "__main__":
    run_local_test()