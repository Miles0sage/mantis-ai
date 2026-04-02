from mantis.core.query_engine import QueryEngine

class App:
    def __init__(self):
        self.query_engine = QueryEngine()

    def run(self):
        # Example queries to demonstrate token tracking
        queries = [
            "What is the weather today?",
            "Tell me about quantum computing",
            "How do I make pizza dough?"
        ]
        
        for query in queries:
            response = self.query_engine.process_query(query)
            print(f"Query: {query}")
            print(f"Response: {response}")
            print("---")
        
        # Print usage statistics after running
        usage = self.query_engine.get_usage()
        print(f"Total Input Tokens: {usage['input_tokens']}")
        print(f"Total Output Tokens: {usage['output_tokens']}")
        print(f"Estimated Cost (USD): ${usage['estimated_cost_usd']:.6f}")
        
        return usage

if __name__ == "__main__":
    app = App()
    app.run()
