import asyncio
from datetime import datetime
import heapq
import os
import queue
import sys
import time
from math import ceil
from pathlib import Path
from typing import Any, List, Tuple

import chromadb
from chromadb.api.types import IncludeEnum
from chromadb.types import Metadata
from chromadb.utils.embedding_functions.sentence_transformer_embedding_function import (
    SentenceTransformerEmbeddingFunction,
)
from PIL import Image
from sentence_transformers import util
from tqdm.asyncio import tqdm

from .utils import calculate_file_hashes, chunkify

MODEL_NAME = "clip-ViT-B-32"
DB_PATH_NAME = "database"


class ImageAnalyzer:
    def __init__(self):
        self._setup_database()

    def _setup_database(self):
        self.client = chromadb.PersistentClient(path=self._get_database_path())
        embedding_function: Any = SentenceTransformerEmbeddingFunction(
            model_name=MODEL_NAME, device=util.get_device_name()
        )
        self.collection = self.client.get_or_create_collection(
            name="image_embeddings",
            metadata={"hnsw:space": "cosine"},
            embedding_function=embedding_function,
        )

    @staticmethod
    async def _load_image(filepath: str):
        image = Image.open(filepath)
        return image

    @staticmethod
    async def _async_load_images(batch_paths: list[str]) -> list[Any]:
        images = await asyncio.gather(
            *[ImageAnalyzer._load_image(path) for path in batch_paths]
        )
        return images

    @staticmethod
    def _get_database_path():
        if os.environ.get("APP_ENV") != "production":
            return "./" + DB_PATH_NAME

        if getattr(sys, "frozen", False):
            app_path = Path(sys.executable).parent.parent
            db_path = app_path / "Contents" / "Resources" / DB_PATH_NAME
        else:
            db_path = Path("./" + DB_PATH_NAME)

        if sys.platform == "darwin":
            db_path = (
                Path.home()
                / "Library"
                / "Application Support"
                / "SnapSweep"
                / DB_PATH_NAME
            )

        # Ensure the directory exists
        db_path.mkdir(parents=True, exist_ok=True)
        return str(db_path)

    async def add_images(
        self, image_paths: list[str], path_to_hash_map: dict[str, str]
    ):
        """
        Adds the given image paths to the database.

        Parameters:
            image_paths (list[str]): A list of image paths to add to the database.
            path_to_hash_map (dict[str, str]): A dictionary mapping image paths to their hash values.
        """
        images = await ImageAnalyzer._async_load_images(image_paths)
        image_hashes = [path_to_hash_map[path] for path in image_paths]

        self.collection.add(
            ids=image_hashes,
            images=images,
            metadatas=[
                {
                    "path": path,
                    "deleted": False,
                }
                for path in image_paths
            ],
        )

    def update_metadata(self, update_path_to_hash_map: dict[str, str]):
        ids = list(update_path_to_hash_map.values())
        self.collection.update(
            ids=ids,
            metadatas=[
                {
                    "path": path,
                    "deleted": False,
                }
                for path in update_path_to_hash_map.keys()
            ],
        )

    async def update_image_index(self, path_to_hash_map: dict[str, str]):
        """
        Updates the image index with the given image paths.
        """

        image_paths = list(path_to_hash_map.keys())
        image_hashes = list(path_to_hash_map.values())
        existing_hashes = self.collection.get(ids=image_hashes)["ids"]
        new_image_paths = [
            path
            for path, hash_value in path_to_hash_map.items()
            if hash_value not in existing_hashes
        ]

        updated_image_paths = self.collection.get(
            ids=image_hashes, where={"path": {"$nin": list(image_paths)}}
        )["ids"]

        if updated_image_paths:
            print(
                f"Detected {len(updated_image_paths)} updated image paths, updating metadata...",
            )
            hash_path_mapping = {v: k for k, v in path_to_hash_map.items()}
            update_path_to_hash_map = {
                hash_path_mapping[image_hash]: image_hash
                for image_hash in updated_image_paths
            }
            self.update_metadata(update_path_to_hash_map)
            print("Metadata updated.")

        if len(new_image_paths) > 0:
            print(f"Creating embeddings for {len(new_image_paths)} images...")
            start_time = time.time()
            with tqdm(
                total=len(new_image_paths), desc="Creating embeddings"
            ) as progress_bar:
                CHUNK_SIZE = ceil(len(new_image_paths) / 10)
                total_new_embeddings = 0
                for chunk in chunkify(new_image_paths, chunk_size=CHUNK_SIZE):
                    await self.add_images(chunk, path_to_hash_map)
                    total_new_embeddings += len(chunk)
                    progress_bar.update(len(chunk))
            print(
                f"Created embeddings for {total_new_embeddings} images in {time.time() - start_time:.2f} seconds"
            )

        else:
            print("All images are already embedded.")

    @staticmethod
    def paraphrase_mining_embeddings(
        embeddings: List[chromadb.Embeddings],
        metadatas: List[chromadb.Metadata],
        top_k=100,
        max_pairs=500000,
        query_chunk_size=5000,
        corpus_chunk_size=100000,
    ) -> List[tuple[float, str, str]]:
        """
        Finds near-duplicate images based on embeddings stored in the database.

        Args:
            embeddings: torch.Tensor
                The embeddings to search for near-duplicates.
            top_k: int
                The number of near-duplicates to find.
            max_pairs: int
                The maximum number of near-duplicates to find.
            query_chunk_size: int
                The size of the query chunk.
            corpus_chunk_size: int
                The size of the corpus chunk.
            metadatas: chromadb.Metadata
                The metadatas to use for the search.
        Returns:
            list of tuple: A list containing tuples of similarity score and the paths of the near-duplicate images.
        """

        top_k += 1
        pairs = queue.PriorityQueue()
        min_score = -1
        num_added = 0
        import torch

        for corpus_start_idx in range(0, len(embeddings), corpus_chunk_size):
            for query_start_idx in range(0, len(embeddings), query_chunk_size):
                scores = util.cos_sim(
                    torch.tensor(
                        embeddings[query_start_idx : query_start_idx + query_chunk_size]
                    ),
                    torch.tensor(
                        embeddings[
                            corpus_start_idx : corpus_start_idx + corpus_chunk_size
                        ]
                    ),
                )

                scores_top_k_values, scores_top_k_idx = torch.topk(
                    scores,
                    min(top_k, len(scores[0])),
                    dim=1,
                    largest=True,
                    sorted=False,
                )
                scores_top_k_values = scores_top_k_values.cpu().tolist()
                scores_top_k_idx = scores_top_k_idx.cpu().tolist()

                for query_itr in range(len(scores)):
                    for top_k_idx, corpus_itr in enumerate(scores_top_k_idx[query_itr]):
                        i = query_start_idx + query_itr
                        j = corpus_start_idx + corpus_itr

                        if (
                            i != j
                            and scores_top_k_values[query_itr][top_k_idx] > min_score
                        ):
                            pairs.put((scores_top_k_values[query_itr][top_k_idx], i, j))
                            num_added += 1

                            if num_added >= max_pairs:
                                entry = pairs.get()
                                min_score = entry[0]

        # Get the pairs
        added_pairs = set()  # Used for duplicate detection
        pairs_list: List[tuple[float, str, str]] = []
        while not pairs.empty():
            score, i, j = pairs.get()
            sorted_i, sorted_j = sorted([i, j])

            if sorted_i != sorted_j and (sorted_i, sorted_j) not in added_pairs:
                added_pairs.add((sorted_i, sorted_j))
                pairs_list.append(
                    (score, metadatas[sorted_i]["path"], metadatas[sorted_j]["path"])
                )

        # Highest scores first
        pairs_list = sorted(pairs_list, key=lambda x: x[0], reverse=True)
        return pairs_list

    @staticmethod
    def paraphrase_mining_embeddings_v2(
        embeddings: List[List[float]],
        metadatas: List[Metadata],
        top_k: int = 100,
        max_pairs: int = 500000,
        query_chunk_size: int = 5000,
        corpus_chunk_size: int = 100000,
        similarity_threshold: float = 0.5,
    ) -> List[Tuple[float, str, str]]:
        import torch
        from sentence_transformers import util

        pairs = []
        embeddings_tensor = torch.tensor(embeddings)
        total_embeddings = len(embeddings)

        for corpus_start_idx in range(0, total_embeddings, corpus_chunk_size):
            corpus_end_idx = min(corpus_start_idx + corpus_chunk_size, total_embeddings)
            corpus_embeddings = embeddings_tensor[corpus_start_idx:corpus_end_idx]

            for query_start_idx in range(0, total_embeddings, query_chunk_size):
                query_end_idx = min(
                    query_start_idx + query_chunk_size, total_embeddings
                )
                query_embeddings = embeddings_tensor[query_start_idx:query_end_idx]

                scores = util.cos_sim(query_embeddings, corpus_embeddings)

                # Apply similarity threshold
                high_scores = scores >= similarity_threshold

                if high_scores.any():
                    scores_top_k_values, scores_top_k_idx = torch.topk(
                        scores,
                        min(top_k, scores.size(1)),
                        dim=1,
                        largest=True,
                        sorted=False,
                    )

                    for query_itr, (values, indices) in enumerate(
                        zip(scores_top_k_values, scores_top_k_idx)
                    ):
                        i = query_start_idx + query_itr
                        for score, j in zip(values, indices):
                            j = corpus_start_idx + j.item()
                            if (
                                i < j and score >= similarity_threshold
                            ):  # Avoid duplicate pairs and self-comparisons
                                heapq.heappush(pairs, (-score.item(), i, j))
                                if len(pairs) > max_pairs:
                                    heapq.heappop(pairs)

                if len(pairs) >= max_pairs:
                    break

            if len(pairs) >= max_pairs:
                break

        # Convert to final format
        result = [
            (-neg_score, metadatas[i]["path"], metadatas[j]["path"])
            for neg_score, i, j in heapq.nlargest(max_pairs, pairs)
        ]

        return result

    async def similarity_search(
        self,
        path_to_hash_map: dict[str, str],
        top_k=10,
        limit: int | None = None,
        threshold=0.9,
    ) -> List[tuple[float, str, str]]:
        """
        Search for near duplicates using the given image embeddings.

        Parameters:
            image_paths (list[str]): A list of image paths to search for near duplicates.
            top_k (int): The number of near duplicates to find.
            limit (int): The maximum number of near duplicates to find.

        Returns:
            list: A list of tuples containing the similarity score, the paths of the two images.
        """
        image_hashes = list(path_to_hash_map.values())
        all_docs = self.collection.get(
            ids=image_hashes,
            where={"deleted": False},
            limit=limit,
            include=[IncludeEnum.embeddings, IncludeEnum.metadatas],
        )
        embeddings: List[Any] = all_docs["embeddings"] or []
        metadatas: List[Any] = all_docs["metadatas"] or []

        near_duplicates = self.paraphrase_mining_embeddings_v2(
            embeddings=embeddings,
            top_k=top_k,
            metadatas=metadatas,
            similarity_threshold=threshold,
        )

        if limit is not None:
            near_duplicates = near_duplicates[:limit]

        return near_duplicates

    @staticmethod
    def remove_invalid_pairs(near_duplicates: list[tuple[float, str, str]]):
        """
        Generate image pairs from the near duplicates list.

        Ignores images that are not in the image folder.

        Parameters:
            near_duplicates (list): A list of tuples containing the similarity score, the paths of the two images.

        Returns:
            list: A list of tuples containing the image names, the indices of the two images, and the similarity score.
        """
        import os

        valid_pairs = [
            (score, img1_path, img2_path)
            for score, img1_path, img2_path in near_duplicates
            if os.path.exists(img1_path) and os.path.exists(img2_path)
        ]

        return valid_pairs

    async def mark_images_as_deleted(self, image_paths: list[str]):
        path_to_hash_map = await calculate_file_hashes(image_paths)
        self.collection.update(
            ids=list(path_to_hash_map.values()),
            metadatas=[
                {"deleted": True, "deleted_at": datetime.now().isoformat()}
                for _ in image_paths
            ],
        )
