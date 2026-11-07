"""Small development bootstrap using the real ComfyUI server and executor.

Only the native image/mask nodes and this source plugin are initialized. Queue
handling follows ComfyUI main.py (GPL-3.0); no models or unrelated extensions
are initialized for image-node validation.
"""

import argparse
import asyncio
import logging
from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_comfy import setup


def serve(comfy, scratch, port):
    setup(comfy, scratch)
    import execution
    from server import PromptServer
    from app.assets.manager import NoAssets
    from comfy.cli_args import args

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    manager = NoAssets(args)
    server = PromptServer(loop, asset_manager=manager)
    server.add_routes()

    def worker():
        executor = execution.PromptExecutor(server, cache_type=execution.CacheType.CLASSIC,
                                            cache_args={"ram": 16., "ram_inactive": 4.}, asset_manager=manager)
        while True:
            item, item_id = server.prompt_queue.get()
            prompt_id = item[1]
            try:
                executor.execute(item[2], prompt_id, item[3], item[4])
                server.prompt_queue.task_done(
                    item_id, executor.history_result,
                    status=execution.PromptQueue.ExecutionStatus(
                        status_str="success" if executor.success else "error",
                        completed=executor.success, messages=executor.status_messages),
                    process_item=lambda prompt: prompt[:5] + prompt[6:])
                server.send_sync("executing", {"node": None, "prompt_id": prompt_id}, server.client_id)
            except Exception:
                logging.exception("Development queue failed")

    threading.Thread(target=worker, daemon=True).start()

    async def start():
        await server.setup()
        await asyncio.gather(server.start_multi_address([("127.0.0.1", port)], verbose=True), server.publish_loop())

    try:
        loop.run_until_complete(start())
    finally:
        if server.client_session is not None:
            loop.run_until_complete(server.client_session.close())
        loop.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comfy", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    sys.argv = [sys.argv[0]]
    logging.basicConfig(level=logging.INFO)
    serve(args.comfy, args.scratch, args.port)
