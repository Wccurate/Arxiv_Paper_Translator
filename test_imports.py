try:
    import argparse
    print("argparse ok")
    import src.context
    print("src.context ok")
    import src.parser
    print("src.parser ok")
    import src.translator
    print("src.translator ok")
    import src.walker
    print("src.walker ok")
    import src.compiler
    print("src.compiler ok")
    print("All imports ok")
except Exception as e:
    print(f"Import failed: {e}")
