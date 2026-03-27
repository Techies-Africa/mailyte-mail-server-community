# Proxy worker — Postfix milter interface on port 11332
bind_socket = "*:11332";

milter = yes;

timeout = 120s;

upstream "local" {
    default = yes;
    self_scan = yes;
}
