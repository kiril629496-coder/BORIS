#!/bin/bash

echo "🚀 Starting BORIS..."

docker compose up -d

cd ../frontend
npm run dev