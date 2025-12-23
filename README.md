# Auto Database Analyzer

Auto Database Analyzer is a practical AI assistant for working with large SQL Server environments. The goal is simple: make complex databases and legacy SQL easier to understand, document, and improve without spending hours digging through views, procedures, and long scripts.

It ingests database objects such as tables, views, stored procedures, and functions, then uses retrieval + embeddings (RAG) to answer questions grounded in your own database context. You can ask questions in plain English, get explanations of what a query or view is doing, and explains long SQL scripts step-by-step, and get performance improvements plus suggested code changes for existing DB objects and SQL scripts, while keeping humans in control of the final changes.

Unlike many existing systems that stop at a high level schema view or return generic SQL, this project builds separate, purpose-built indexes for different entities like tables, columns, views, stored procedures, and functions. Because each entity is stored and retrieved independently with its own metadata, the answers and suggested code changes stay grounded in the real structure and logic of your database.


## Tech stack

This project is built with Python and Flask for the application/API layer, and a vector database (Pinecone) for semantic retrieval. The pipeline stores structured metadata and embeddings so the assistant can respond with more accurate, explainable answers.
