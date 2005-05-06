class DFS:
    def __init__(self, dep_func, dep_args):
        self._ordering    = []
        self._seen_nodes  = {}
        self._dep_func    = dep_func
        self._dep_args    = dep_args
        return

    def _node_seen(self, node, others):
        self._stack.pop()

        if self._seen_nodes[node] == 0:
            self._ordering.append(node)
            self._seen_nodes[node] = 1

        if len(others) > 0:
            self._stack.append((others[0], others[1:]))

        return

    def search(self, node):
        self._stack = stack = [(node, [])]

        while len(stack):
            node, others = stack[-1]

            if self._seen_nodes.has_key(node):
                self._node_seen(node, others)
                continue

            self._seen_nodes[node] = 0

            deps = self._dep_func(node, self._dep_args)

            if len(deps) > 0:
                stack.append((deps[0], deps[1:]))
                continue

            self._node_seen(node, others)

        return

    def result(self):
        return self._ordering
