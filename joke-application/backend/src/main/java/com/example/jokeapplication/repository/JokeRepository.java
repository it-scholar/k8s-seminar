package com.example.jokeapplication.repository;

import com.example.jokeapplication.entity.Joke;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.stereotype.Repository;

import java.util.Optional;

@Repository
public interface JokeRepository extends JpaRepository<Joke, Long> {

    @Query(value = "SELECT * FROM jokes ORDER BY RANDOM() LIMIT 1", nativeQuery = true)
    Optional<Joke> findRandom();
}
