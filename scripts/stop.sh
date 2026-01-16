#!/bin/bash

cd ..

if [ -f backend.pid ]; then
    kill $(cat backend.pid)
    rm backend.pid
    echo "Backend stopped."
else
    echo "No backend process found."
fi

if [ -f frontend.pid ]; then
    kill $(cat frontend.pid)
    rm frontend.pid
    echo "Frontend stopped."
else
    echo "No frontend process found."
fi

cd scripts

