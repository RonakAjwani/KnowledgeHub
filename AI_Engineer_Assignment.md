# Assignment

## Project
KnowledgeHub — Multi-Document RAG Assistant with Chat Memory

## Problem Statement
Build an app where users can upload multiple documents and chat with an assistant that answers using retrieved context, remembers conversation history, and cites sources.

## Requirements
- Upload/manage multiple documents (PDF/txt/md)
- Chunk + embed + store in a vector DB (Pinecone/Qdrant)
- Multi-turn chat with context memory (follow-up questions should work)
- Answers grounded in retrieved chunks, with source citations
- Store conversations in a DB (Postgres/SQLite/Mongo)
- Clean REST API with proper error handling

## Tech Stack
- Frontend: React/Next.js
- Backend: Python FastAPI
- AI: LangChain (or similar)
- Vector DB: Pinecone or Qdrant
- Database: PostgreSQL/SQLite/MongoDB

## Deliverables
- Source code (GitHub repo)
- README (setup + architecture + design decisions)
- Deployed live link (or Docker Compose as fallback)
- 5–8 min demo video
- Basic tests

## Bonus
- Streaming responses
- Auth
- Hybrid search/re-ranking
- CI pipeline

## Time Estimate
1–3 days
